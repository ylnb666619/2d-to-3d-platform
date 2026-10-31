/* The page's own controls: adding a drawing, and watching the read happen.
 *
 * The reading is Python and lives behind /api; this file is only the wire. It
 * stays quiet when the page is opened from disk, where there is no /api to
 * talk to -- the reading itself still works, just not re-running it.
 *
 * A fresh open asks for a drawing first. The page can show the last reading on
 * its own, but "here is a finished model" without the user having added
 * anything is a step skipped, not a convenience.
 */
(function () {
  'use strict';

  var openBtn = document.getElementById('openBtn');
  var modal = document.getElementById('openModal');
  var fileInput = document.getElementById('dxfFile');
  var pathInput = document.getElementById('dxfPath');
  var groundInput = document.getElementById('groundPath');
  var runBtn = document.getElementById('runJob');
  var closeBtn = document.getElementById('closeModal');
  var logBox = document.getElementById('jobLog');
  var lastNote = document.getElementById('lastNote');

  var dropped = null;
  var running = false;
  var served = false;

  function addLog(text) {
    if (!logBox) return;
    var line = document.createElement('div');
    line.textContent = text;
    logBox.appendChild(line);
    logBox.scrollTop = logBox.scrollHeight;
  }

  function show(visible) {
    if (modal) modal.hidden = !visible;
  }

  function hasInput() {
    if (fileInput && fileInput.files && fileInput.files.length) return true;
    if (dropped) return true;
    return Boolean(pathInput && pathInput.value.trim());
  }

  function syncRun() {
    if (!runBtn || running) return;
    runBtn.disabled = !hasInput();
  }

  // The button only exists if the page is served by massing's own server.
  fetch('/api/status')
    .then(function (response) { return response.json(); })
    .then(function (state) {
      served = true;
      if (openBtn) openBtn.hidden = false;
      if (kernelBox) kernelBox.hidden = false;
      if (pathInput && !pathInput.value && state.source) pathInput.value = state.source;
      if (groundInput && !groundInput.value && state.ground) groundInput.value = state.ground;
      if (lastNote && state.source) {
        var name = state.source.split(/[\\/]/).pop();
        lastNote.textContent = '上次读的图纸已填好：' + name + '　直接点「识图并建模」，或者换一张。';
        lastNote.hidden = false;
      }
      syncRun();
      // Arriving fresh (no job timestamp in the address) means the user has
      // not chosen anything in this session yet: ask before showing a model.
      // A reload of the same window is not a fresh arrival, and a ?v= cache
      // buster is not a job either.
      var asked = false;
      try { asked = window.sessionStorage.getItem('massing.asked') === '1'; } catch (error) {}
      var afterJob = /[?&]t=/.test(window.location.search);
      if (!afterJob && !asked) {
        try { window.sessionStorage.setItem('massing.asked', '1'); } catch (error) {}
        show(true);
      }
    })
    .catch(function () { /* opened from disk: no server, no button */ });

  if (openBtn) openBtn.addEventListener('click', function () { show(true); syncRun(); });
  if (closeBtn) closeBtn.addEventListener('click', function () { if (!running) show(false); });
  if (modal) {
    modal.addEventListener('click', function (event) {
      if (event.target === modal && !running) show(false);
    });
  }
  if (fileInput) fileInput.addEventListener('change', syncRun);
  if (pathInput) pathInput.addEventListener('input', syncRun);

  window.addEventListener('dragover', function (event) { event.preventDefault(); });
  window.addEventListener('drop', function (event) {
    event.preventDefault();
    var files = event.dataTransfer && event.dataTransfer.files;
    if (!files || !files.length) return;
    var file = files[0];
    if (!/\.dxf$/i.test(file.name)) return;
    dropped = file;
    if (modal) {
      show(true);
      addLog('已拖入：' + file.name);
      syncRun();
    }
  });

  function payload() {
    var form = new FormData();
    var file = (fileInput && fileInput.files && fileInput.files[0]) || dropped;
    if (file) form.append('file', file, file.name);
    else form.append('path', (pathInput && pathInput.value || '').trim());
    var ground = (groundInput && groundInput.value || '').trim();
    if (ground) form.append('ground', ground);
    return { form: form, file: file };
  }

  function poll(id) {
    fetch('/api/jobs/' + id)
      .then(function (response) { return response.json(); })
      .then(function (job) {
        if (!job.ok) { finish(job.error || '任务丢失'); return; }
        if (job.state === 'running') {
          if (logBox) {
            logBox.textContent = '';
            (job.log || []).forEach(function (entry) {
              addLog(entry.seconds.toFixed(1) + 's　' + entry.text);
            });
            addLog('…　' + job.seconds.toFixed(0) + ' 秒');
          }
          setTimeout(function () { poll(id); }, 700);
          return;
        }
        if (job.state === 'done') {
          addLog('完成，正在打开…');
          location.replace(job.url);
          return;
        }
        finish(job.error || '失败');
      })
      .catch(function (error) { finish(String(error)); });
  }

  function finish(message) {
    running = false;
    syncRun();
    addLog(message);
  }

  if (runBtn) {
    runBtn.addEventListener('click', function () {
      if (running) return;
      var data = payload();
      if (!data.file && !(pathInput && pathInput.value.trim())) {
        addLog('选一个文件，或者填一个路径。');
        return;
      }
      running = true;
      runBtn.disabled = true;
      if (logBox) logBox.textContent = '';
      addLog('开始识图…');
      fetch('/api/jobs', { method: 'POST', body: data.form })
        .then(function (response) { return response.json(); })
        .then(function (job) {
          if (!job.ok) { finish(job.error || '启动失败'); return; }
          poll(job.id);
        })
        .catch(function (error) { finish(String(error)); });
    });
  }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !running) show(false);
  });

  // --- the kernel: STEP and IFC, and the viewer that opens them ---------
  var exportStep = document.getElementById('exportStep');
  var exportBoth = document.getElementById('exportBoth');
  var openViewer = document.getElementById('openViewer');
  var kernelBox = document.getElementById('kernelBox');
  var exportLog = document.getElementById('exportLog');
  var exporting = false;

  function exportLine(text) {
    if (!exportLog) return;
    var row = document.createElement('div');
    row.textContent = text;
    exportLog.appendChild(row);
    exportLog.scrollTop = exportLog.scrollHeight;
  }

  function exportButtons() {
    var busy = exporting;
    if (exportStep) exportStep.disabled = busy;
    if (exportBoth) exportBoth.disabled = busy;
  }

  function exportPoll(id) {
    fetch('/api/jobs/' + id)
      .then(function (response) { return response.json(); })
      .then(function (job) {
        if (job.state === 'running') {
          if (exportLog) {
            exportLog.textContent = '';
            (job.log || []).forEach(function (entry) { exportLine(entry.text); });
            exportLine('…　' + job.seconds.toFixed(0) + ' 秒　' + (job.step || ''));
          }
          setTimeout(function () { exportPoll(id); }, 1000);
          return;
        }
        exporting = false;
        exportButtons();
        if (job.state === 'done') {
          var files = job.files || {};
          var names = Object.keys(files).map(function (key) {
            return files[key].split(/[\\/]/).pop();
          });
          if (exportLog) exportLog.textContent = '';
          exportLine('完成：' + names.join('、') + '　' + job.seconds.toFixed(0) + ' 秒');
          if (openViewer) openViewer.disabled = false;
        } else {
          exportLine('失败：' + (job.error || '未知错误'));
        }
      })
      .catch(function (error) { exporting = false; exportButtons(); exportLine(String(error)); });
  }

  function startExport(schema, button) {
    if (exporting) return;
    if (!served) { exportLine('要在本机服务里打开页面才能导出。'); return; }
    exporting = true;
    exportButtons();
    if (exportLog) exportLog.textContent = '';
    exportLine('开始…（内核建模，约 40 秒）');
    fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ schema: schema })
    })
      .then(function (response) { return response.json(); })
      .then(function (job) {
        if (!job.ok) {
          exporting = false;
          exportButtons();
          exportLine(job.error || '启动失败');
          return;
        }
        exportPoll(job.id);
      })
      .catch(function (error) { exporting = false; exportButtons(); exportLine(String(error)); });
  }

  if (exportStep) exportStep.addEventListener('click', function () { startExport('none', exportStep); });
  if (exportBoth) exportBoth.addEventListener('click', function () { startExport('both', exportBoth); });

  if (openViewer) {
    openViewer.addEventListener('click', function () {
      fetch('/api/viewer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      })
        .then(function (response) { return response.json(); })
        .then(function (result) {
          if (result.ok) window.open(result.url, '_blank');
          else exportLine('查看器没起来：' + (result.error || ''));
        })
        .catch(function (error) { exportLine(String(error)); });
    });
  }

  // The kernel block is only useful when there is a server behind the page.

  // A window left open while the drawing is read again is showing an old
  // building. The server knows when the page was last written; if that is
  // later than this page loaded, reload -- unless the user is mid-choice or
  // mid-job, where a reload would throw away what they are doing.
  var loadedAt = Date.now() / 1000;
  setInterval(function () {
    if (!served || running) return;
    if (modal && !modal.hidden) return;
    fetch('/api/status')
      .then(function (response) { return response.json(); })
      .then(function (state) {
        if (state.version && Number(state.version) > loadedAt + 2) {
          window.location.reload();
        }
      })
      .catch(function () {});
  }, 5000);
})();
