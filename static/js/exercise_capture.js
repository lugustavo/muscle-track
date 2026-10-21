/* Exercise animation from two photos (core/exercise_animation.py joins them on save).
   - the two file fields work anywhere: pick the start and the finish pictures from the gallery;
   - with a camera and a secure page, "Gravar com a câmera" opens a full-screen guided camera (same look and guides as the
     assessment photos, static/js/photo_guides.js): step 1 the starting position, step 2 the finishing position with the
     first photo laid over the image so the phone stays in the same place and the framing matches;
   - a live preview alternates the two pictures at the chosen speed, as the animation will play. */
(function () {
    'use strict';
    var gettext = window.gettext || function (s) { return s; };
    var box = document.querySelector('[data-anim]');
    if (!box) return;

    var startInput = box.querySelector('[data-anim-start]');
    var endInput = box.querySelector('[data-anim-end]');
    var speedSelect = box.querySelector('[name=anim_speed]');
    var preview = box.querySelector('[data-anim-preview]');
    var previewImg = box.querySelector('[data-anim-img]');
    var recordBox = box.querySelector('[data-anim-record]');
    var openBtn = box.querySelector('[data-anim-open]');
    var note = box.querySelector('[data-anim-help]');
    var live = document.querySelector('[data-anim-live]');
    var SPEEDS = {slow: 1200, normal: 800, fast: 500};
    var MAX_SIDE = 1000;

    function say(text) { if (note) { note.textContent = text; note.hidden = !text; } }

    // ---- the preview: the two pictures alternating ----------------------------------------------------------------
    var urls = [null, null];
    var timer = null;
    var shown = 0;

    function revoke() { urls.forEach(function (u) { if (u) URL.revokeObjectURL(u); }); urls = [null, null]; }

    function refreshPreview() {
        clearInterval(timer);
        timer = null;
        revoke();
        if (!(startInput.files[0] && endInput.files[0])) { preview.hidden = true; return; }
        urls = [URL.createObjectURL(startInput.files[0]), URL.createObjectURL(endInput.files[0])];
        previewImg.src = urls[0];
        preview.hidden = false;
        shown = 0;
        var every = SPEEDS[speedSelect.value] || SPEEDS.normal;
        timer = setInterval(function () { shown = 1 - shown; previewImg.src = urls[shown]; }, every);
    }
    [startInput, endInput, speedSelect].forEach(function (el) { el.addEventListener('change', refreshPreview); });
    refreshPreview();

    function setFile(input, blob, name) {
        try {
            var transfer = new DataTransfer();
            transfer.items.add(new File([blob], name, {type: 'image/jpeg'}));
            input.files = transfer.files;
        } catch (e) { return false; }
        return true;
    }

    // ---- the guided camera --------------------------------------------------------------------------------------------
    var canLive = !!(live && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.isSecureContext);
    if (!canLive) {
        if (recordBox) recordBox.hidden = true;
        if (live && !window.isSecureContext) say(gettext('A câmera guiada só funciona em página segura (https). Escolha as fotos da galeria.'));
        return;
    }
    recordBox.hidden = false;

    var video = live.querySelector('video');
    var frame = live.querySelector('[data-anim-frame]');
    var stage = live.querySelector('[data-anim-stage]');
    var guidesBox = live.querySelector('[data-anim-guides]');
    var ghostImg = live.querySelector('[data-anim-ghost]');
    var countBox = live.querySelector('[data-anim-count]');
    var shutter = live.querySelector('[data-anim-shoot]');
    var titleEl = live.querySelector('[data-anim-title]');
    var tipEl = live.querySelector('[data-anim-tip]');
    var stream = null;
    var step = 0;
    var shots = [null, null];
    var ghostUrl = null;
    var countdown = null;

    function recall(key, fallback) { try { var v = localStorage.getItem('mt-anim-' + key); return v === null ? fallback : v; } catch (e) { return fallback; } }
    function remember(key, value) { try { localStorage.setItem('mt-anim-' + key, String(value)); } catch (e) { /* ignore */ } }
    var prefs = {
        guides: recall('guides', '1') === '1', ghost: recall('ghost', '1') === '1',
        timer: parseInt(recall('timer', '5'), 10) || 0,   // the trainer is usually the one in front of the camera
        horizontal: recall('orient', 'v') === 'h', facing: recall('facing', 'environment') === 'user' ? 'user' : 'environment'
    };

    function ratio() { return prefs.horizontal ? 4 / 3 : 3 / 4; }

    function layout() {   // the biggest frame of the chosen proportion that fits between the bars
        var w = stage.clientWidth, h = stage.clientHeight;
        var width = Math.min(w, h * ratio());
        frame.style.width = Math.floor(width) + 'px';
        frame.style.height = Math.floor(width / ratio()) + 'px';
    }

    function draw() {
        guidesBox.replaceChildren();
        guidesBox.hidden = !prefs.guides;
        if (window.PhotoGuides) guidesBox.appendChild(window.PhotoGuides.svg('', {levels: false}));
        live.querySelector('[data-anim-toggle-guides]').setAttribute('aria-pressed', prefs.guides ? 'true' : 'false');
        var ghostBtn = live.querySelector('[data-anim-toggle-ghost]');
        ghostBtn.hidden = step !== 1;
        ghostBtn.setAttribute('aria-pressed', prefs.ghost ? 'true' : 'false');
        ghostImg.hidden = !(step === 1 && prefs.ghost && ghostUrl);
        live.querySelector('[data-anim-orient]').hidden = step !== 0;     // both photos must have the same proportion
        live.querySelector('[data-anim-orient-label]').textContent = prefs.horizontal ? live.dataset.textHorizontal : live.dataset.textVertical;
        live.querySelector('[data-anim-timer-label]').textContent = prefs.timer ? prefs.timer + ' s' : live.dataset.textNoTimer;
        live.querySelector('[data-anim-timer]').classList.toggle('on', !!prefs.timer);
        live.querySelector('[data-anim-retake]').hidden = step !== 1;
        titleEl.textContent = step === 0 ? live.dataset.textStart : live.dataset.textEnd;
        tipEl.textContent = step === 0 ? live.dataset.textStartTip : live.dataset.textEndTip;
        layout();
    }

    function cancelCountdown() {
        if (countdown) clearInterval(countdown);
        countdown = null;
        countBox.hidden = true;
        shutter.classList.remove('counting');
    }

    function stopLive() {
        cancelCountdown();
        if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
        stream = null;
        video.srcObject = null;
        live.hidden = true;
        document.documentElement.classList.remove('cam-open');
        window.removeEventListener('resize', layout);
        if (ghostUrl) { URL.revokeObjectURL(ghostUrl); ghostUrl = null; }
    }

    function openStream() {
        if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
        var constraints = {video: {facingMode: {ideal: prefs.facing}, width: {ideal: 1280}, height: {ideal: 1280}}, audio: false};
        return navigator.mediaDevices.getUserMedia(constraints).then(function (s) {
            stream = s;
            video.srcObject = s;
            frame.classList.toggle('mirror', prefs.facing === 'user');   // a selfie preview is mirrored; the saved photo is not
            return video.play();
        });
    }

    function startLive() {
        say('');
        step = 0;
        shots = [null, null];
        live.hidden = false;
        document.documentElement.classList.add('cam-open');
        draw();
        window.addEventListener('resize', layout);
        openStream().then(function () {
            layout();
            if (navigator.mediaDevices.enumerateDevices) {
                navigator.mediaDevices.enumerateDevices().then(function (devices) {
                    live.querySelector('[data-anim-flip]').hidden = devices.filter(function (d) { return d.kind === 'videoinput'; }).length < 2;
                }).catch(function () { /* the flip button just stays hidden */ });
            }
        }).catch(function () {
            stopLive();
            say(gettext('Não foi possível usar a câmera (permissão negada ou câmera ocupada). Escolha as fotos da galeria.'));
        });
    }

    // The frame is cropped from the video as it is shown (object-fit: cover), so the photo is what was framed
    function shoot() {
        var vw = video.videoWidth, vh = video.videoHeight;
        if (!vw || !vh) return;
        var sw = vw, sh = vh;
        if (vw / vh > ratio()) { sw = vh * ratio(); } else { sh = vw / ratio(); }
        var sx = (vw - sw) / 2, sy = (vh - sh) / 2;
        var scale = Math.min(1, MAX_SIDE / Math.max(sw, sh));
        var canvas = document.createElement('canvas');
        canvas.width = Math.round(sw * scale);
        canvas.height = Math.round(sh * scale);
        canvas.getContext('2d').drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
        frame.classList.remove('cam-flash');
        void frame.offsetWidth;   // restart the flash animation
        frame.classList.add('cam-flash');
        canvas.toBlob(function (blob) {
            if (!blob) return;
            shots[step] = blob;
            if (step === 0) {
                if (ghostUrl) URL.revokeObjectURL(ghostUrl);
                ghostUrl = URL.createObjectURL(blob);
                ghostImg.src = ghostUrl;
                step = 1;
                draw();
                return;
            }
            setFile(startInput, shots[0], 'inicio.jpg');
            setFile(endInput, shots[1], 'fim.jpg');
            stopLive();
            refreshPreview();
            say(gettext('Pronto! Veja a prévia e salve o exercício para guardar a animação.'));
        }, 'image/jpeg', 0.9);
    }

    function startCountdown() {
        var left = prefs.timer;
        shutter.classList.add('counting');
        countBox.hidden = false;
        countBox.textContent = left;
        countdown = setInterval(function () {
            left -= 1;
            if (left <= 0) { cancelCountdown(); shoot(); return; }
            countBox.textContent = left;
        }, 1000);
    }

    shutter.addEventListener('click', function () {
        if (countdown) { cancelCountdown(); return; }
        if (prefs.timer) { startCountdown(); } else { shoot(); }
    });
    openBtn.addEventListener('click', startLive);
    live.querySelector('[data-anim-close]').addEventListener('click', stopLive);
    document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && !live.hidden) stopLive(); });
    live.querySelector('[data-anim-flip]').addEventListener('click', function () {
        prefs.facing = prefs.facing === 'user' ? 'environment' : 'user';
        remember('facing', prefs.facing);
        openStream().catch(function () { say(gettext('Não foi possível trocar de câmera.')); });
    });
    live.querySelector('[data-anim-toggle-guides]').addEventListener('click', function () { prefs.guides = !prefs.guides; remember('guides', prefs.guides ? '1' : '0'); draw(); });
    live.querySelector('[data-anim-toggle-ghost]').addEventListener('click', function () { prefs.ghost = !prefs.ghost; remember('ghost', prefs.ghost ? '1' : '0'); draw(); });
    live.querySelector('[data-anim-orient]').addEventListener('click', function () { prefs.horizontal = !prefs.horizontal; remember('orient', prefs.horizontal ? 'h' : 'v'); draw(); });
    live.querySelector('[data-anim-timer]').addEventListener('click', function () {
        prefs.timer = prefs.timer === 0 ? 3 : (prefs.timer === 3 ? 5 : (prefs.timer === 5 ? 10 : 0));
        remember('timer', prefs.timer);
        draw();
    });
    live.querySelector('[data-anim-retake]').addEventListener('click', function () {
        cancelCountdown();
        step = 0;
        shots = [null, null];
        draw();
    });
    window.addEventListener('pagehide', stopLive);
})();
