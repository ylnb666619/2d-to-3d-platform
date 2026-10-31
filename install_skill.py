"""Install the BIM capability and the text fixes into the local text-to-cad plugin.

The plugin lives in a versioned cache directory that an update replaces, so run
this again after `claude plugin update text-to-cad@earthtojake`.

    python install_skill.py

It copies skills/bim/SKILL.md into the newest installed plugin and re-applies
the documentation patches kept in C:\\CAD-Viewer\\skill-fixes.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

CACHE_ROOT = Path.home() / ".claude" / "plugins" / "cache" / "earthtojake" / "text-to-cad"
HERE = Path(__file__).resolve().parent
SKILL_SOURCE = HERE / "skill" / "SKILL.md"
PATCH_SCRIPT = HERE.parent / "skill-fixes" / "apply.py"


def newest_plugin() -> Path | None:
    candidates = [path for path in CACHE_ROOT.glob("*") if (path / "skills").is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: tuple(int(part) for part in path.name.split(".") if part.isdigit()) or (0,))
    return candidates[-1]


def main() -> int:
    plugin = newest_plugin()
    if plugin is None:
        print(f"没有找到已安装的插件：{CACHE_ROOT}", file=sys.stderr)
        return 1

    if not SKILL_SOURCE.is_file():
        print(f"缺少 skill 源文件：{SKILL_SOURCE}", file=sys.stderr)
        return 1

    target = plugin / "skills" / "bim"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SKILL_SOURCE, target / "SKILL.md")
    print(f"已安装 skill：{target / 'SKILL.md'}")

    if PATCH_SCRIPT.is_file():
        print("\n应用文档补丁：")
        result = subprocess.run([sys.executable, str(PATCH_SCRIPT)], capture_output=True, text=True)
        print(result.stdout.strip())
        if result.returncode not in (0, 2):
            print(result.stderr.strip(), file=sys.stderr)
            return result.returncode
    else:
        print(f"未找到补丁脚本：{PATCH_SCRIPT}")

    print("\n完成。重启 Claude Code 后生效；skill 名为 text-to-cad:bim。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
