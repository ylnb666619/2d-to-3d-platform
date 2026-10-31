/* The model tab: three.js over the scene the pipeline emitted.
 *
 * The building arrives as prisms -- footprint rings with a bottom and a top --
 * and every prism carries the name of the component it came from. Nothing here
 * knows what a wall is; it draws prisms and reports which one was clicked.
 *
 * Kept in its own file so the page can embed it verbatim and so it reads as
 * JavaScript rather than as a string full of doubled braces.
 */
(function () {
  'use strict';

  var DATA = (function () {
    var node = document.getElementById('sceneData');
    if (!node) return null;
    try { return JSON.parse(node.textContent); } catch (error) { return null; }
  })();

  var state = { floor: 'all', categories: null, explode: 0, cut: 1, selected: null };
  var HL_COLOUR = 0xf59e0b;

  // Filled in by init3d, once the module has loaded. buildPrism runs in this
  // scope, so three.js has to live here too, not inside init3d.
  var THREE = null;

  function clamp(value, lo, hi) { return value < lo ? lo : (value > hi ? hi : value); }

  // A prism: the two caps triangulated, the sides as quads. three.js has the
  // ear clipper already (ShapeUtils), so this is the whole of the geometry.
  function buildPrism(group, item) {
    var rings = item.r;
    if (!rings || !rings.length || rings[0].length < 3) return;
    var contour = rings[0].map(function (p) { return new THREE.Vector2(p[0], p[1]); });
    var holes = [];
    var h;
    for (h = 1; h < rings.length; h += 1) {
      if (rings[h].length >= 3) {
        holes.push(rings[h].map(function (p) { return new THREE.Vector2(p[0], p[1]); }));
      }
    }
    var faces = null;
    try { faces = THREE.ShapeUtils.triangulateShape(contour, holes); } catch (error) { faces = null; }
    var points = contour.concat.apply(contour, holes);
    var z0 = item.z[0];
    var z1 = item.z[1];
    var start = group.pos.length / 3;

    function push(point, height) {
      group.pos.push(point.x, height, -point.y);
      group.ids.push(item.id);
    }

    var f;
    if (faces && faces.length) {
      for (f = 0; f < faces.length; f += 1) {
        var a = faces[f][0], b = faces[f][1], c = faces[f][2];
        push(points[a], z1); push(points[b], z1); push(points[c], z1);
        push(points[a], z0); push(points[c], z0); push(points[b], z0);
      }
    }
    var every = [contour].concat(holes);
    var r;
    for (r = 0; r < every.length; r += 1) {
      var ring = every[r];
      for (var i = 0; i < ring.length; i += 1) {
        var p = ring[i], q = ring[(i + 1) % ring.length];
        push(p, z0); push(q, z0); push(q, z1);
        push(p, z0); push(q, z1); push(p, z1);
      }
    }
    item.range = { start: start, count: group.pos.length / 3 - start };
    item.group = group;
  }

  async function init3d() {
    var canvas = document.getElementById('view3d');
    if (!canvas || state.started) return;
    state.started = true;
    if (!DATA || !DATA.items || !DATA.items.length) return;

    var module;
    try {
      module = await import('./vendor/three.module.js');
    } catch (error) {
      document.getElementById('modelPane').innerHTML =
        '<div class="empty">three.js 没加载到（vendor/three.module.js）。<br>' +
        '请用本机服务打开：http://127.0.0.1:3246</div>';
      return;
    }
    THREE = module.default || module;

    var pane = document.getElementById('modelPane');
    var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.localClippingEnabled = true;

    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b1220);

    var bounds = DATA.bounds;
    var span = Math.max(
      bounds.x[1] - bounds.x[0], bounds.y[1] - bounds.y[0], bounds.z[1] - bounds.z[0], 1
    );
    var camera = new THREE.PerspectiveCamera(42, 1, Math.max(span * 0.002, 0.05), span * 60);

    var centre = new THREE.Vector3(
      (bounds.x[0] + bounds.x[1]) / 2,
      (bounds.z[0] + bounds.z[1]) / 2,
      -(bounds.y[0] + bounds.y[1]) / 2
    );

    var view = { theta: -0.72, phi: 0.98, radius: span * 1.45, target: centre.clone() };
    var goal = { theta: view.theta, phi: view.phi, radius: view.radius, target: centre.clone() };

    // --- lights ---------------------------------------------------------
    scene.add(new THREE.HemisphereLight(0xdfe9f5, 0x1b2436, 1.9));
    var key = new THREE.DirectionalLight(0xffffff, 2.1);
    key.position.set(span * 0.8, span * 1.6, span * 0.9);
    scene.add(key);
    var fill = new THREE.DirectionalLight(0x9bc4ff, 0.75);
    fill.position.set(-span, span * 0.7, -span * 1.2);
    scene.add(fill);

    // --- materials ------------------------------------------------------
    var MATERIAL = {};
    DATA.categories.forEach(function (cat) {
      var glass = cat === 'window' || cat === 'room';
      MATERIAL[cat] = new THREE.MeshStandardMaterial({
        color: new THREE.Color(CAT_COLOUR[cat] || '#cbd5e1'),
        roughness: 0.86,
        metalness: 0.02,
        flatShading: true,
        side: THREE.DoubleSide,
        transparent: glass,
        opacity: cat === 'window' ? 0.5 : (cat === 'room' ? 0.5 : 1),
        depthWrite: !glass
      });
    });
    var HL_MAT = new THREE.MeshBasicMaterial({
      color: HL_COLOUR,
      transparent: true,
      opacity: 0.55,
      depthTest: false,
      side: THREE.DoubleSide
    });

    // --- geometry -------------------------------------------------------
    var groups = new Map();
    DATA.items.forEach(function (item, index) {
      item.id = index;
      var groupKey = item.f + '|' + item.c;
      var group = groups.get(groupKey);
      if (!group) {
        group = { floor: item.f, cat: item.c, pos: [], ids: [], items: [] };
        groups.set(groupKey, group);
      }
      buildPrism(group, item);
      if (item.range) group.items.push(item);
    });

    var meshes = [];
    groups.forEach(function (group) {
      var geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(group.pos, 3));
      geometry.setAttribute('item', new THREE.Float32BufferAttribute(group.ids, 1));
      geometry.computeVertexNormals();
      geometry.computeBoundingSphere();
      var mesh = new THREE.Mesh(geometry, MATERIAL[group.cat] || MATERIAL.wall);
      mesh.userData.group = group;
      mesh.frustumCulled = false;
      group.mesh = mesh;
      group.geometry = geometry;
      scene.add(mesh);
      meshes.push(mesh);
    });

    var gridSize = Math.max(bounds.x[1] - bounds.x[0], bounds.y[1] - bounds.y[0]) * 1.35;
    var grid = new THREE.GridHelper(gridSize, 24, 0x334155, 0x1e293b);
    grid.position.set(centre.x, bounds.z[0] - 0.02, centre.z);
    scene.add(grid);

    // --- floors and layers ----------------------------------------------
    var floors = DATA.floors.slice().sort(function (a, b) { return a.n - b.n; });
    var floorIndex = {};
    floors.forEach(function (floor, index) { floorIndex[floor.n] = index; });

    var floorList = document.getElementById('floorList');
    if (floorList) {
      var all = document.createElement('button');
      all.textContent = '全部';
      all.className = 'active';
      all.addEventListener('click', function () { setFloor('all', all); });
      floorList.appendChild(all);
      floors.forEach(function (floor) {
        var chip = document.createElement('button');
        chip.textContent = floor.n + 'F';
        chip.title = floor.title + '　' + floor.z.toFixed(2) + 'm';
        chip.addEventListener('click', function () { setFloor(floor.n, chip); });
        floorList.appendChild(chip);
      });
    }

    function setFloor(which, button) {
      state.floor = which;
      if (floorList) {
        floorList.querySelectorAll('button').forEach(function (node) {
          node.className = node === button ? 'active' : '';
        });
      }
      if (which === 'all') {
        goal.target.copy(centre);
        goal.radius = span * 1.45;
        goal.phi = 0.98;
      } else {
        var floor = floors.filter(function (item) { return item.n === which; })[0];
        if (floor) {
          goal.target.set(centre.x, floor.z + floor.h / 2, centre.z);
          goal.radius = span * 0.95;
          goal.phi = 0.9;
        }
      }
      refresh();
    }

    var catBoxes = Array.prototype.slice.call(document.querySelectorAll('input[data-cat]'));
    state.categories = new Set(catBoxes.map(function (box) { return box.dataset.cat; }));
    catBoxes.forEach(function (box) {
      if (!DATA.counts[box.dataset.cat]) {
        box.checked = false;
        box.disabled = true;
        state.categories.delete(box.dataset.cat);
      }
      box.addEventListener('change', function () {
        if (box.checked) state.categories.add(box.dataset.cat);
        else state.categories.delete(box.dataset.cat);
        refresh();
      });
    });

    var highlight = null;

    function refresh() {
      groups.forEach(function (group) {
        var shown = state.categories.has(group.cat) &&
          (state.floor === 'all' || state.floor === group.floor);
        group.mesh.visible = shown;
        group.mesh.position.y = (floorIndex[group.floor] || 0) * state.explode * span * 0.06;
      });
      if (highlight && state.selected && state.selected.group) {
        highlight.position.y = state.selected.group.mesh.position.y;
      }
    }

    // --- cut plane -------------------------------------------------------
    var plane = new THREE.Plane(new THREE.Vector3(0, -1, 0), 0);
    function applyCut() {
      var on = state.cut < 0.999;
      plane.constant = bounds.z[0] + (bounds.z[1] - bounds.z[0]) * state.cut;
      Object.keys(MATERIAL).forEach(function (name) {
        MATERIAL[name].clippingPlanes = on ? [plane] : null;
      });
      HL_MAT.clippingPlanes = on ? [plane] : null;
      var label = document.getElementById('cutValue');
      if (label) label.textContent = on ? plane.constant.toFixed(2) + ' m' : '关';
    }

    var cutSlider = document.getElementById('cut');
    if (cutSlider) {
      cutSlider.addEventListener('input', function () {
        state.cut = Number(cutSlider.value) / 100;
        applyCut();
      });
    }
    var explodeSlider = document.getElementById('explode');
    if (explodeSlider) {
      explodeSlider.addEventListener('input', function () {
        state.explode = Number(explodeSlider.value) / 100;
        var label = document.getElementById('explodeValue');
        if (label) label.textContent = Math.round(state.explode * 100) + '%';
        refresh();
      });
    }

    // --- picking ---------------------------------------------------------
    var raycaster = new THREE.Raycaster();
    var pointer = new THREE.Vector2();
    var hoverTag = document.getElementById('hoverTag');

    function itemAt(event) {
      var rect = canvas.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      var visible = meshes.filter(function (mesh) { return mesh.visible; });
      var hits = raycaster.intersectObjects(visible, false);
      for (var i = 0; i < hits.length; i += 1) {
        var hit = hits[i];
        var attribute = hit.object.geometry.getAttribute('item');
        if (!attribute || !hit.face) continue;
        return DATA.items[attribute.getX(hit.face.a)];
      }
      return null;
    }

    function highlightItem(item) {
      if (highlight) {
        scene.remove(highlight);
        highlight.geometry.dispose();
        highlight = null;
      }
      state.selected = item;
      if (!item || !item.range) { showPick(null); return; }
      var group = item.group;
      var geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', group.geometry.getAttribute('position'));
      geometry.setDrawRange(item.range.start, item.range.count);
      geometry.boundingSphere = group.geometry.boundingSphere;
      highlight = new THREE.Mesh(geometry, HL_MAT);
      highlight.renderOrder = 6;
      highlight.frustumCulled = false;
      highlight.position.y = group.mesh.position.y;
      scene.add(highlight);
      showPick(item);
    }

    function showPick(item) {
      var box = document.getElementById('pick');
      if (!box) return;
      box.textContent = '';
      var card = document.createElement('div');
      card.className = 'pickCard';
      var name = document.createElement('div');
      name.className = 'name';
      var meta = document.createElement('div');
      meta.className = 'meta';
      if (!item) {
        name.textContent = '在模型里单击一个构件';
        meta.textContent = '颜色区分构件类别，楼层可以单独看。';
      } else {
        name.textContent = item.n;
        var bits = [CAT_LABEL[item.c] || item.c, item.f + 'F'];
        bits.push('面积 ' + item.a.toFixed(2) + ' m²');
        bits.push('体积 ' + item.v.toFixed(2) + ' m³');
        bits.push('标高 ' + item.z[0].toFixed(2) + '–' + item.z[1].toFixed(2) + ' m');
        if (item.tag) bits.push('编号 ' + item.tag);
        meta.textContent = bits.join('　·　');
      }
      card.appendChild(name);
      card.appendChild(meta);
      box.appendChild(card);
    }

    // --- statistics ------------------------------------------------------
    var statsBox = document.getElementById('sceneStats');
    if (statsBox) {
      var total = 0;
      var volume = 0;
      DATA.categories.forEach(function (cat) {
        var count = DATA.counts[cat] || 0;
        total += count;
        var row = document.createElement('div');
        row.className = 'statRow';
        var left = document.createElement('span');
        left.textContent = CAT_LABEL[cat] || cat;
        var right = document.createElement('span');
        right.textContent = count + ' 个';
        row.appendChild(left);
        row.appendChild(right);
        statsBox.appendChild(row);
        var badge = document.querySelector('[data-count="' + cat + '"]');
        if (badge) badge.textContent = count;
      });
      DATA.items.forEach(function (item) { volume += item.v; });
      var sum = document.createElement('div');
      sum.className = 'statRow';
      var sumLeft = document.createElement('span');
      sumLeft.textContent = '合计';
      var sumRight = document.createElement('span');
      sumRight.textContent = total + ' 个 · ' + volume.toFixed(0) + ' m³';
      sum.appendChild(sumLeft);
      sum.appendChild(sumRight);
      statsBox.appendChild(sum);
    }

    // --- camera controls -------------------------------------------------
    var pointers = new Map();
    var drag = null;

    function panBy(dx, dy) {
      var right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0);
      var up = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
      var scale = view.radius * 0.0016;
      goal.target.addScaledVector(right, -dx * scale).addScaledVector(up, dy * scale);
      view.target.copy(goal.target);
    }

    canvas.addEventListener('pointerdown', function (event) {
      canvas.setPointerCapture(event.pointerId);
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pointers.size === 1) {
        var panning = event.button === 2 || event.button === 1 || event.shiftKey;
        drag = { mode: panning ? 'pan' : 'orbit', moved: 0 };
      } else if (pointers.size === 2) {
        var two = Array.from(pointers.values());
        drag = {
          mode: 'pinch',
          moved: 99,
          distance: Math.hypot(two[0].x - two[1].x, two[0].y - two[1].y)
        };
      }
    });

    canvas.addEventListener('pointermove', function (event) {
      var prior = pointers.get(event.pointerId);
      if (!prior) { hover(event); return; }
      if (!drag) return;
      var dx = event.clientX - prior.x;
      var dy = event.clientY - prior.y;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      drag.moved += Math.abs(dx) + Math.abs(dy);
      if (drag.mode === 'orbit') {
        goal.theta -= dx * 0.007;
        goal.phi = clamp(goal.phi - dy * 0.007, 0.05, 1.6);
        view.theta = goal.theta;
        view.phi = goal.phi;
      } else if (drag.mode === 'pan') {
        panBy(dx, dy);
      } else if (drag.mode === 'pinch' && pointers.size === 2) {
        var now = Array.from(pointers.values());
        var distance = Math.hypot(now[0].x - now[1].x, now[0].y - now[1].y);
        if (drag.distance > 0) {
          goal.radius = clamp(goal.radius * (drag.distance / Math.max(distance, 1)), span * 0.1, span * 8);
          view.radius = goal.radius;
        }
        drag.distance = distance;
      }
      if (hoverTag) hoverTag.hidden = true;
    });

    function endPointer(event) {
      pointers.delete(event.pointerId);
      if (drag && drag.mode === 'orbit' && drag.moved < 6) {
        highlightItem(itemAt(event));
      }
      if (pointers.size === 0) drag = null;
    }
    canvas.addEventListener('pointerup', endPointer);
    canvas.addEventListener('pointercancel', endPointer);
    canvas.addEventListener('contextmenu', function (event) { event.preventDefault(); });

    canvas.addEventListener('wheel', function (event) {
      event.preventDefault();
      var factor = Math.exp(event.deltaY * 0.0011);
      goal.radius = clamp(goal.radius * factor, span * 0.05, span * 8);
      view.radius = goal.radius;
    }, { passive: false });

    var hoverPending = false;
    var hoverEvent = null;
    function hover(event) {
      hoverEvent = event;
      if (hoverPending) return;
      hoverPending = true;
      requestAnimationFrame(function () {
        hoverPending = false;
        if (!hoverEvent || !hoverTag) return;
        var item = itemAt(hoverEvent);
        if (!item) {
          hoverTag.hidden = true;
          canvas.style.cursor = 'default';
          return;
        }
        var rect = pane.getBoundingClientRect();
        hoverTag.textContent = item.n;
        hoverTag.style.left = (hoverEvent.clientX - rect.left) + 'px';
        hoverTag.style.top = (hoverEvent.clientY - rect.top) + 'px';
        hoverTag.hidden = false;
        canvas.style.cursor = 'pointer';
      });
    }

    // --- view presets ----------------------------------------------------
    var viewButtons = Array.prototype.slice.call(document.querySelectorAll('[data-view]'));
    var PRESET = {
      iso: { theta: -0.72, phi: 0.98, radius: 1.45 },
      front: { theta: 0.0, phi: 1.5, radius: 1.55 },
      side: { theta: Math.PI / 2, phi: 1.5, radius: 1.55 },
      top: { theta: 0.0, phi: 0.06, radius: 1.5 },
      fit: { theta: -0.72, phi: 0.98, radius: 1.45 }
    };
    viewButtons.forEach(function (button) {
      button.addEventListener('click', function () {
        var preset = PRESET[button.dataset.view] || PRESET.iso;
        goal.theta = preset.theta;
        goal.phi = preset.phi;
        goal.radius = span * preset.radius;
        if (state.floor === 'all') goal.target.copy(centre);
        viewButtons.forEach(function (node) { node.className = ''; });
        button.className = 'active';
      });
    });

    // --- size and loop ---------------------------------------------------
    function resize() {
      var width = pane.clientWidth;
      var height = pane.clientHeight;
      if (!width || !height) return;
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }
    if (window.ResizeObserver) new ResizeObserver(resize).observe(pane);
    window.addEventListener('resize', resize);
    resize();

    applyCut();
    refresh();
    showPick(null);

    function frame() {
      requestAnimationFrame(frame);
      if (pane.hidden) return;
      view.theta += (goal.theta - view.theta) * 0.16;
      view.phi += (goal.phi - view.phi) * 0.16;
      view.radius += (goal.radius - view.radius) * 0.16;
      view.target.lerp(goal.target, 0.16);
      var sinPhi = Math.sin(view.phi);
      camera.position.set(
        view.target.x + view.radius * sinPhi * Math.sin(view.theta),
        view.target.y + view.radius * Math.cos(view.phi),
        view.target.z + view.radius * sinPhi * Math.cos(view.theta)
      );
      camera.lookAt(view.target);
      renderer.render(scene, camera);
    }
    frame();
  }

  window.init3d = init3d;
})();
