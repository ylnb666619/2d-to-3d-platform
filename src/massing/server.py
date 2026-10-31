"""The front end's own server: it serves the page and runs the pipeline.

The reading is Python -- ezdxf, shapely, the whole of it -- and the browser
cannot do it. So the browser asks, and this answers: drop a DXF on the page (or
type a path) and the same machine reads it, assembles the building, writes the
page and hands back the address.

A job runs in a thread and reports its step, because a 建施图 takes tens of
seconds and a spinner that says nothing for that long is indistinguishable from
a hang.
"""

from __future__ import annotations

import json
import mimetypes
import re
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

BUILDERS: dict[str, "Job"] = {}
BUILDERS_LOCK = threading.Lock()

# The CAD Viewer the text-to-cad skill installs; it is what opens a STEP.
VIEWER_URL = "http://127.0.0.1:3245/"

WAITING_PAGE = """<!doctype html><meta charset="utf-8">
<title>重装武神 · 准备中</title>
<style>
  body { margin:0; height:100vh; display:grid; place-items:center; background:#0b1220;
         color:#e2e8f0; font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
  .card { text-align:center; max-width:640px; padding:32px; }
  h1 { font-size:16px; font-weight:600; letter-spacing:.06em; margin:0 0 10px; }
  p { color:#94a3b8; margin:6px 0; }
  code { background:#1e293b; padding:1px 6px; border-radius:5px; color:#cbd5e1; }
  .bar { height:3px; width:220px; margin:22px auto 0; background:#1e293b; border-radius:3px;
         overflow:hidden; }
  .bar i { display:block; width:40%; height:100%; background:#38bdf8; border-radius:3px;
           animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { 0% { transform: translateX(-100%); } 100% { transform: translateX(320%); } }
</style>
<div class="card">
  <h1>重装武神</h1>
  <p id="line">正在启动…</p>
  <p><code>__SOURCE__</code></p>
  <div class="bar"><i></i></div>
</div>
<script>
  const line = document.getElementById('line');
  async function poll() {
    try {
      const state = await (await fetch('/api/status')).json();
      if (state.ready) { location.replace('/reading.html?t=' + Date.now()); return; }
      if (state.job) {
        line.textContent = state.job.error ? state.job.error : ('正在' + (state.job.step || '读图') + '…');
      } else {
        line.textContent = '还没有读图。把 DXF 拖进页面，或者关闭这个窗口。';
      }
    } catch (error) {}
    setTimeout(poll, 700);
  }
  poll();
</script>
"""


@dataclass
class Job:
    id: str
    state: str = "running"
    step: str = "排队"
    kind: str = "read"  # "read" | "export" | "build"
    log: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    url: str = ""
    files: dict[str, str] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "kind": self.kind,
            "step": self.step,
            "seconds": round((self.finished or time.time()) - self.started, 1),
            "log": self.log[-12:],
            "error": self.error,
            "url": self.url,
            "files": self.files,
            "result": self.result,
        }


def _remember(root: Path, source: str, ground: str) -> None:
    """The last drawing, so the shortcut can start without naming one."""
    try:
        (root / "last.json").write_text(
            json.dumps({"source": source, "ground": ground}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 - a memory that cannot be written is fine
        pass


def _recall(root: Path) -> tuple[str, str]:
    try:
        data = json.loads((root / "last.json").read_text(encoding="utf-8"))
        return data.get("source", "") or "", data.get("ground", "") or ""
    except Exception:  # noqa: BLE001
        return "", ""


class App:
    """What the handler needs to know: where the page lives, what to read."""

    def __init__(self, root: Path, *, source: str = "", ground: str = "") -> None:
        self.root = root
        self.source = source
        self.ground = ground
        self.lock = threading.Lock()

    # --- jobs -----------------------------------------------------------
    def job(self, job_id: str) -> Job | None:
        with BUILDERS_LOCK:
            return BUILDERS.get(job_id)

    def latest(self) -> Job | None:
        with BUILDERS_LOCK:
            jobs = sorted(BUILDERS.values(), key=lambda job: job.started)
        return jobs[-1] if jobs else None

    def start(self, source: str, ground: str = "") -> Job:
        """Begin a read. One at a time: two pipelines in one process is not
        two pipelines, it is two pipelines sharing one interpreter, and both
        end up several times slower than either alone."""
        with BUILDERS_LOCK:
            for existing in BUILDERS.values():
                if existing.state == "running":
                    raise RuntimeError("上一张图还在读，等它读完再开始。")
            job = Job(id=uuid.uuid4().hex[:12], kind="read")
            BUILDERS[job.id] = job
        thread = threading.Thread(target=self._run, args=(job, source, ground), daemon=True)
        thread.start()
        return job

    def start_export(self, schema: str = "both") -> Job:
        """The kernel build: STEP, and IFC beside it.

        It runs in its own process, not in this one. build123d loads a full
        CAD kernel and holds it; the page server has to stay answerable while
        it works, and a kernel that dies must not take the page with it.
        """
        if not self.source:
            raise RuntimeError("还没有读入图纸，先添加图纸。")
        if not Path(self.source).exists():
            raise RuntimeError(f"找不到图纸：{self.source}")
        with BUILDERS_LOCK:
            for existing in BUILDERS.values():
                if existing.state == "running":
                    raise RuntimeError("上一个任务还在跑，等它结束。")
            job = Job(id=uuid.uuid4().hex[:12], kind="export")
            BUILDERS[job.id] = job
        thread = threading.Thread(
            target=self._run_export, args=(job, self.source, self.ground, schema), daemon=True
        )
        thread.start()
        return job

    def _run_export(self, job: Job, source: str, ground: str, schema: str) -> None:
        import os
        import subprocess
        from datetime import datetime

        from . import history

        folder = self.root / "models"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        step = folder / f"step-{stamp}.step"
        ifc = folder / f"step-{stamp}.ifc"
        args = [
            sys.executable, "-u", "-m", "massing", "massing", source,
            "--building", "--out", str(step),
        ]
        if ground:
            args += ["--ground-dxf", ground]
        if schema in {"IFC4", "IFC2X3", "both"}:
            args += ["--ifc", str(ifc), "--ifc-schema", schema]
        import massing

        source_root = str(Path(massing.__file__).resolve().parents[1])
        env = dict(os.environ)
        env["PYTHONPATH"] = source_root + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        env.setdefault("PYTHONIOENCODING", "utf-8")
        job.step = "拉伸、楼板、门窗、IFC（约两分钟）"
        try:
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=source_root,
            )
            assert process.stdout is not None
            for line in process.stdout:
                text = line.rstrip()
                if text:
                    job.step = text[:80]
                    job.log.append(
                        {"text": text, "seconds": round(time.time() - job.started, 1)}
                    )
            code = process.wait()
            if code != 0:
                raise RuntimeError(f"内核建模失败（退出码 {code}）")
            if not step.exists():
                raise RuntimeError("内核没有写出 STEP 文件。")
            job.files["step"] = str(step)
            if "--ifc" in args and ifc.exists():
                job.files["ifc"] = str(ifc)
            # Keep the browser copy with the model, so the STEP and the view
            # stay the same building even after the page is rebuilt.
            scene_copy = step.with_suffix(".scene.json")
            try:
                source_scene = self.root / "scene.json"
                if source_scene.exists():
                    scene_copy.write_text(
                        source_scene.read_text(encoding="utf-8"), encoding="utf-8"
                    )
            except Exception:  # noqa: BLE001
                pass
            history.record_model(
                self.root,
                {
                    "file": str(step),
                    "scene": str(scene_copy) if scene_copy.exists() else "",
                    "kind": "export",
                    "name": f"{Path(source).stem} 的 STEP",
                    "source": source,
                    "ifc": str(ifc) if ifc.exists() else "",
                },
            )
            job.step = "完成"
            job.state = "done"
        except Exception as exc:  # noqa: BLE001 - the page has to hear about it
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.step = "失败"
        finally:
            job.finished = time.time()

    def _run(self, job: Job, source: str, ground: str) -> None:
        from . import history
        from .pipeline import read_drawing
        from .webui import write_html

        def note(_state: str, text: str) -> None:
            job.step = text
            job.log.append({"text": text, "seconds": round(time.time() - job.started, 1)})

        try:
            reading = read_drawing(source, ground=ground or None, on_step=note)
            target = self.root / "reading.html"
            write_html(
                reading.views,
                str(target),
                source=source,
                scene=reading.scene,
            )
            if reading.scene:
                (self.root / "scene.json").write_text(
                    json.dumps(reading.scene, ensure_ascii=False), encoding="utf-8"
                )
            with self.lock:
                self.source = source
                self.ground = ground
            _remember(self.root, source, ground)
            history.record(
                self.root,
                source,
                ground,
                summary={
                    "plans": len(reading.views),
                    "items": len((reading.scene or {}).get("items") or []),
                    "floors": len((reading.scene or {}).get("floors") or []),
                },
            )
            job.step = "完成"
            job.url = "/reading.html?t=" + str(int(time.time() * 1000))
            job.state = "done"
        except Exception as exc:  # noqa: BLE001 - the browser has to hear about it
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.step = "失败"
            job.log.append({"text": traceback.format_exc()[-1200:], "seconds": 0})
        finally:
            job.finished = time.time()

    def start_build(self, text: str) -> Job:
        """One sentence -> a massing block, in the kernel, in its own process."""
        text = (text or "").strip()
        if not text:
            raise RuntimeError("先写一句话，比如「3 层，层高 3.6，40×18」。")
        with BUILDERS_LOCK:
            for existing in BUILDERS.values():
                if existing.state == "running":
                    raise RuntimeError("上一个任务还在跑，等它结束。")
            job = Job(id=uuid.uuid4().hex[:12], kind="build")
            BUILDERS[job.id] = job
        thread = threading.Thread(target=self._run_build, args=(job, text), daemon=True)
        thread.start()
        return job

    def _run_build(self, job: Job, text: str) -> None:
        import os
        import subprocess
        from datetime import datetime

        import massing
        from . import history

        folder = self.root / "models"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        step = folder / f"model-{stamp}.step"
        result_file = folder / f"model-{stamp}.json"
        source_root = str(Path(massing.__file__).resolve().parents[1])
        env = dict(os.environ)
        env["PYTHONPATH"] = source_root + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        env.setdefault("PYTHONIOENCODING", "utf-8")
        args = [
            sys.executable, "-u", "-m", "massing.preset", text,
            "--step", str(step), "--json", str(result_file),
        ]
        job.step = "读这句话"
        try:
            process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", env=env, cwd=source_root,
            )
            assert process.stdout is not None
            for line in process.stdout:
                stripped = line.rstrip()
                if stripped:
                    job.step = stripped[:80]
                    job.log.append(
                        {"text": stripped, "seconds": round(time.time() - job.started, 1)}
                    )
            code = process.wait()
            if code != 0:
                raise RuntimeError(f"生成失败（退出码 {code}）")
            if not step.exists():
                raise RuntimeError("没有生成 STEP 文件。")
            try:
                job.result = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - the file is the report, not essential
                job.result = {}
            job.files["step"] = str(step)
            spec = job.result.get("spec") or {}
            history.record_model(
                self.root,
                {
                    "file": str(step),
                    "scene": job.result.get("scene", ""),
                    "kind": "generated",
                    "name": (
                        f"{spec.get('storeys', '?')} 层 "
                        f"{spec.get('lengthM', '?')}×{spec.get('widthM', '?')} m"
                    ),
                    "text": text,
                    "sentence": job.result.get("sentence", ""),
                    "solids": job.result.get("solids", 0),
                    "volume": job.result.get("volume", 0),
                },
            )
            job.step = "完成"
            job.state = "done"
        except Exception as exc:  # noqa: BLE001
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.step = "失败"
        finally:
            job.finished = time.time()

    def start_pdf(self, source: str) -> Job:
        """A sheet that is only pixels: no layers, no text, just ink."""
        source = (source or "").strip()
        if not source or not Path(source).exists():
            raise RuntimeError(f"找不到这个 PDF：{source}")
        with BUILDERS_LOCK:
            for existing in BUILDERS.values():
                if existing.state == "running":
                    raise RuntimeError("上一个任务还在跑，等它结束。")
            job = Job(id=uuid.uuid4().hex[:12], kind="pdf")
            BUILDERS[job.id] = job
        thread = threading.Thread(target=self._run_pdf, args=(job, source), daemon=True)
        thread.start()
        return job

    def _run_pdf(self, job: Job, source: str) -> None:
        from . import history
        from .pdfread import build_page

        def note(text: str) -> None:
            job.step = text
            job.log.append({"text": text, "seconds": round(time.time() - job.started, 1)})

        try:
            note(f"打开 {Path(source).name}")
            result = build_page(
                source,
                self.root,
                on_page=lambda index, count, reading: note(
                    f"第 {index}/{count} 页：{len(reading.rooms)} 个房间"
                ),
            )
            job.files["html"] = result["html"]
            job.result = {"pages": result["pages"], "rooms": result["rooms"]}
            history.record(
                self.root,
                source,
                "",
                summary={"kind": "pdf", "pages": result["pages"], "items": 0,
                         "plans": result["pages"], "rooms": result["rooms"]},
            )
            job.step = "完成"
            job.url = "/pdf.html?t=" + str(int(time.time() * 1000))
            job.state = "done"
        except Exception as exc:  # noqa: BLE001
            job.state = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.step = "失败"
        finally:
            job.finished = time.time()

    # --- the CAD viewer ------------------------------------------------
    def viewer_url(self, path: str = "") -> str:
        """The viewer is the only thing here that can open a STEP: the page
        draws prisms, a kernel file needs a kernel's viewer."""
        self.ensure_viewer()
        base = VIEWER_URL
        if not path:
            return base
        return f"{base}?file={path.replace(chr(92), '/')}"

    def ensure_viewer(self) -> bool:
        import subprocess
        import urllib.request

        try:
            with urllib.request.urlopen(VIEWER_URL, timeout=2):
                return True
        except Exception:  # noqa: BLE001 - not running yet
            pass
        exe = Path(sys.prefix) / "Scripts" / "cadgen.exe"
        command = str(exe) if exe.exists() else "cadgen"
        try:
            subprocess.run(
                [command, "viewer", "--detach"],
                capture_output=True,
                timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:  # noqa: BLE001
            return False
        for _attempt in range(20):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(VIEWER_URL, timeout=1):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    # --- files ----------------------------------------------------------
    def save_upload(self, filename: str, payload: bytes) -> str:
        """Keep an uploaded drawing, but never keep the same bytes twice.

        Dropping the same file in twice used to leave 建施图.dxf, 建施图-65351b.dxf,
        ... and the page then showed one of the copies as "the drawing".
        """
        import hashlib

        safe = re.sub(r"[^0-9A-Za-z._\-一-鿿]+", "_", Path(filename).name) or "upload.dxf"
        folder = self.root / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(payload).hexdigest()
        target = folder / safe
        if target.exists():
            if hashlib.sha1(target.read_bytes()).hexdigest() == digest:
                return str(target)
            target = folder / f"{target.stem}-{digest[:8]}{target.suffix}"
            if target.exists():
                return str(target)
        target.write_bytes(payload)
        return str(target)


def _multipart(body: bytes, content_type: str) -> dict[str, tuple[str, bytes]]:
    """Form fields and files, without the removed cgi module."""
    header = b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n"
    message = BytesParser(policy=default_policy).parsebytes(header + body)
    fields: dict[str, tuple[str, bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename() or ""
        fields[name] = (filename, part.get_payload(decode=True) or b"")
    return fields


class Handler(SimpleHTTPRequestHandler):
    server_version = "massing-ui"

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # The default log is one line per asset; the interesting lines are the
        # jobs and whether the page was actually fetched or served from cache.
        first = str(args[0]) if args else ""
        if first.startswith("POST /api") or "reading.html" in first:
            super().log_message(format, *args)

    def end_headers(self) -> None:
        # The page is rewritten after every read, and a browser that caches it
        # keeps showing the old building -- and the old UI. Nothing here is
        # worth caching: it is one machine talking to itself.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, text: str, status: int = 200) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _model_page(self, entry: dict[str, Any], scene: dict[str, Any]) -> str:
        """One model, drawn by this app's own viewer -- not the skill's."""
        import html as html_mod

        from .webui import MODEL_LAYERS

        here = Path(__file__).with_name("scene_page.html")
        js = Path(__file__).with_name("model_view.js")
        page = here.read_text(encoding="utf-8")
        colours = {key: colour for key, _label, colour in MODEL_LAYERS}
        labels = {key: label for key, label, _colour in MODEL_LAYERS}
        cats = "".join(
            f'<label><input type="checkbox" data-cat="{key}" checked>'
            f'<i class="swatch" style="background:{colour}"></i>{label}'
            f'<span class="count" data-count="{key}"></span></label>'
            for key, label, colour in MODEL_LAYERS
        )
        title = entry.get("name") or Path(str(entry.get("file"))).stem
        bits = ["一句话建模" if entry.get("kind") == "generated" else "图纸导出"]
        if entry.get("solids"):
            bits.append(f"{entry['solids']} 个实体")
        if entry.get("volume"):
            bits.append(f"{entry['volume']} m³")
        if entry.get("at"):
            bits.append(str(entry["at"]).replace("T", " ")[:16])
        actions = ""
        if entry.get("file") and Path(str(entry["file"])).exists():
            actions = (
                f'<button class="ghost" id="openStep" data-file="'
                f'{html_mod.escape(str(entry["file"]), quote=True)}">用 CAD 查看器看 STEP</button>'
            )
        scene_json = json.dumps(scene, ensure_ascii=False).replace("</", "<\\/")
        return (
            page.replace("__TITLE__", html_mod.escape(str(title)))
            .replace("__META__", html_mod.escape("　·　".join(bits)))
            .replace("__ACTIONS__", actions)
            .replace("__CATS__", cats)
            .replace("__COLOURS__", json.dumps(colours))
            .replace("__LABELS__", json.dumps(labels))
            .replace("__SCENE__", scene_json)
            .replace("__JS__", js.read_text(encoding="utf-8"))
        )

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/view":
            from .history import models

            query = parse_qs(urlparse(self.path).query)
            want = unquote(query.get("m", [""])[0])
            entries = models(self.app.root, 50)
            entry = next((item for item in entries if item.get("file") == want), None)
            if entry is None and entries:
                entry = entries[0]
            if entry is None:
                self._html("<p style='font-family:sans-serif'>还没有生成过模型。</p>", 404)
                return
            scene_file = Path(str(entry.get("scene") or ""))
            if not entry.get("scene") or not scene_file.exists():
                target = self.app.viewer_url(str(entry.get("file") or ""))
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()
                return
            try:
                scene = json.loads(scene_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self._html("<p style='font-family:sans-serif'>这个模型的场景损坏了。</p>", 500)
                return
            self._html(self._model_page(entry, scene))
            return
        if route == "/api/history":
            from .history import recent

            self._json({"ok": True, "items": recent(self.app.root)})
            return
        if route == "/api/models":
            from .history import models

            self._json({"ok": True, "items": models(self.app.root)})
            return
        if route == "/api/status":
            job = self.app.latest()
            page = self.app.root / "reading.html"
            ready = page.exists()
            self._json(
                {
                    "ok": True,
                    "ready": ready,
                    # The page compares this against the moment it loaded, so a
                    # window left open while the drawing is re-read refreshes
                    # itself instead of showing yesterday's building.
                    "version": str(int(page.stat().st_mtime)) if ready else "",
                    "source": self.app.source,
                    "ground": self.app.ground,
                    "job": job.to_dict() if job else None,
                }
            )
            return
        if route.startswith("/api/jobs/"):
            job = self.app.job(route.rsplit("/", 1)[-1])
            if job is None:
                self._json({"ok": False, "error": "没有这个任务"}, 404)
            else:
                self._json({"ok": True, **job.to_dict()})
            return
        if route in ("/", "/index.html"):
            # The home screen: recent drawings, the three modules. It is the
            # page's front door -- the reading page is somewhere you get to.
            home = Path(__file__).with_name("home_page.html")
            if home.exists():
                self._html(home.read_text(encoding="utf-8"))
                return
            if (self.app.root / "reading.html").exists():
                self.send_response(302)
                self.send_header("Location", "/reading.html")
                self.end_headers()
            else:
                source = self.app.source or "还没有指定图纸"
                self._html(WAITING_PAGE.replace("__SOURCE__", _escape(source)))
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/api/pdf":
            body = self._body()
            content_type = self.headers.get("Content-Type") or ""
            source = ""
            if "multipart/form-data" in content_type:
                fields = _multipart(body, content_type)
                name, payload = fields.get("file", ("", b""))
                if name and payload:
                    source = self.app.save_upload(name, payload)
                elif "path" in fields:
                    source = fields["path"][1].decode("utf-8", "replace").strip()
            else:
                try:
                    source = str(json.loads(body.decode("utf-8") or "{}").get("path") or "")
                except Exception:  # noqa: BLE001
                    source = ""
            try:
                job = self.app.start_pdf(source)
            except RuntimeError as exc:
                self._json({"ok": False, "error": str(exc)}, 409)
                return
            self._json({"ok": True, "id": job.id, "kind": job.kind})
            return
        if route == "/api/build":
            body = self._body()
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            try:
                job = self.app.start_build(str(payload.get("text") or ""))
            except RuntimeError as exc:
                self._json({"ok": False, "error": str(exc)}, 409)
                return
            self._json({"ok": True, "id": job.id, "kind": job.kind})
            return
        if route == "/api/preview":
            body = self._body()
            content_type = self.headers.get("Content-Type") or ""
            if "multipart/form-data" in content_type:
                fields = _multipart(body, content_type)
                name, payload = fields.get("file", ("", b""))
                if not name or not payload:
                    self._json({"ok": False, "error": "没有收到文件"}, 400)
                    return
                path = self.app.save_upload(name, payload)
            else:
                try:
                    data = json.loads(body.decode("utf-8") or "{}")
                    path = str(data.get("path") or "")
                except Exception:  # noqa: BLE001
                    path = ""
            if not path or not Path(path).exists():
                self._json({"ok": False, "error": "找不到这个文件"}, 400)
                return
            suffix = Path(path).suffix.lower()
            target = Path(path)
            if suffix in {".dxf", ".pdf"}:
                self._json(
                    {
                        "ok": True,
                        "kind": "pdf" if suffix == ".pdf" else "dxf",
                        "name": target.name,
                        "size": target.stat().st_size,
                        "url": str(target),
                    }
                )
                return
            if suffix in {".step", ".stp", ".ifc", ".glb", ".gltf", ".obj", ".stl"}:
                self._json(
                    {
                        "ok": True,
                        "kind": "mesh",
                        "name": target.name,
                        "size": target.stat().st_size,
                        "url": self.app.viewer_url(str(target)),
                    }
                )
                return
            self._json(
                {"ok": False, "error": f"这个格式打不开：{suffix or '无后缀'}"},
                400,
            )
            return
        if route == "/api/export":
            body = self._body()
            try:
                payload = json.loads(body.decode("utf-8") or "{}") if body else {}
            except Exception:  # noqa: BLE001
                payload = {}
            schema = str(payload.get("schema") or "both")
            try:
                job = self.app.start_export(schema)
            except RuntimeError as exc:
                self._json({"ok": False, "error": str(exc)}, 409)
                return
            self._json({"ok": True, "id": job.id, "kind": job.kind})
            return
        if route == "/api/viewer":
            body = self._body()
            want = ""
            try:
                if body:
                    want = str(json.loads(body.decode("utf-8")).get("file") or "")
            except Exception:  # noqa: BLE001
                want = ""
            step = want or "building.step"
            target = Path(step)
            if not target.is_absolute():
                target = self.app.root / target
            if not want:
                # No file named: the newest model this machine produced.
                from .history import models

                newest = models(self.app.root, 1)
                if newest and newest[0].get("exists"):
                    target = Path(newest[0]["file"])
            ok = self.app.ensure_viewer()
            exists = target.exists()
            url = self.app.viewer_url(str(target)) if exists else VIEWER_URL
            self._json({"ok": ok, "url": url, "file": str(target) if exists else ""})
            return
        if route not in ("/api/jobs", "/api/read"):
            self._json({"ok": False, "error": "未知接口"}, 404)
            return
        body = self._body()
        content_type = self.headers.get("Content-Type") or ""

        source = ""
        ground = ""
        try:
            if "multipart/form-data" in content_type:
                fields = _multipart(body, content_type)
                name, payload = fields.get("file", ("", b""))
                if name and payload:
                    source = self.app.save_upload(name, payload)
                elif "path" in fields:
                    source = fields["path"][1].decode("utf-8", "replace").strip()
                if "ground" in fields and fields["ground"][1]:
                    gname, gpayload = fields["ground"]
                    if gname and gpayload:
                        ground = self.app.save_upload(gname, gpayload)
                    else:
                        ground = gpayload.decode("utf-8", "replace").strip()
            else:
                payload = json.loads(body.decode("utf-8") or "{}")
                source = (payload.get("path") or "").strip()
                ground = (payload.get("ground") or "").strip()
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"请求解析失败：{exc}"}, 400)
            return

        if not source:
            source = self.app.source
        if not source:
            self._json({"ok": False, "error": "没有给文件，也没有给路径。"}, 400)
            return
        if not Path(source).exists():
            self._json({"ok": False, "error": f"找不到文件：{source}"}, 400)
            return

        try:
            job = self.app.start(source, ground)
        except RuntimeError as exc:
            self._json({"ok": False, "error": str(exc)}, 409)
            return
        self._json({"ok": True, "id": job.id, "state": job.state})


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


class _Server(ThreadingHTTPServer):
    # Windows lets a second socket bind the same port when SO_REUSEADDR is on,
    # and then the OS hands each connection to whichever process it likes. The
    # launcher runs every time the shortcut is double-clicked, so a port that
    # answers from seven different processes is a real thing that happened.
    allow_reuse_address = False
    daemon_threads = True


def serve(
    root: str | Path,
    *,
    port: int = 3246,
    host: str = "127.0.0.1",
    source: str = "",
    ground: str = "",
    open_first: bool = True,
) -> None:
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("model/gltf-binary", ".glb")

    root_path = Path(root)
    (root_path / "vendor").mkdir(parents=True, exist_ok=True)
    app = App(root_path, source=source, ground=ground)

    explicit = bool(source)
    if not source:
        source, ground = _recall(root_path)
        app.source, app.ground = source, ground

    handler = lambda *args, **kwargs: Handler(*args, directory=str(root_path), **kwargs)  # noqa: E731
    try:
        httpd = _Server((host, port), handler)  # type: ignore[arg-type]
    except OSError:
        print(
            f"端口 {port} 已被占用（多半是一个旧的静态服务）。先结束它再启动。",
            file=sys.stderr,
        )
        return
    httpd.app = app  # type: ignore[attr-defined]

    # An explicit path is always read. A remembered one is only re-read when
    # the page is missing -- otherwise every launch would spend a minute
    # rebuilding something the user can already see.
    if source and open_first and (explicit or not (root_path / "reading.html").exists()):
        try:
            app.start(source, ground or "")
        except RuntimeError as exc:
            print(f"跳过启动时的读图：{exc}", file=sys.stderr)

    print(f"重装武神：http://{host}:{port}/reading.html　图纸：{source or '未指定'}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
