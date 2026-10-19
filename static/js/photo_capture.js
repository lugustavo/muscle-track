/* Photo form: take the picture with the device camera when there is one, otherwise pick it from the gallery.
   - any device with a camera and a secure page: a full-screen live camera with framing guides (static/js/photo_guides.js):
     the 3:4 frame, grid, level lines, a silhouette for the chosen position and the previous photo of that position laid
     over the image, so every photo is taken the same way. The photo is cropped to the frame, so the proportions never change;
   - phones can also open the native camera app (no guides), and it is the fallback when the live camera is refused;
   - no camera, no permission or no support: the plain file field (gallery/files) with a short explanation.
   The picture is shrunk in the browser (phone photos pass 5 MB) before it is sent. */
(function () {
    // Django's JS catalog (/jsi18n/); identity fallback if it has not loaded
    var gettext = window.gettext || function (s) { return s; };
    var form = document.querySelector('[data-photo-form]');
    if (!form) return;
    var fileInput = form.querySelector('input[type=file]:not([data-phone-camera])');
    var phoneCamera = form.querySelector('[data-phone-camera]');
    var buttons = form.querySelector('[data-capture-buttons]');
    var live = form.querySelector('[data-live]');
    var video = live.querySelector('video');
    var frame = live.querySelector('[data-frame]');
    var stage = live.querySelector('[data-stage]');
    var guidesBox = live.querySelector('[data-guides]');
    var ghostImg = live.querySelector('[data-ghost]');
    var countBox = live.querySelector('[data-count]');
    var shutter = live.querySelector('[data-shoot]');
    var picked = form.querySelector('[data-picked]');
    var note = form.querySelector('[data-capture-note]');
    var angleSelect = form.querySelector('select[name=angle]');
    var assessmentSelect = form.querySelector('select[name=assessment]');   // the student's own form has none
    var stream = null;
    var facing = 'environment';
    var countdown = null;
    var MAX_SIDE = 1800;
    var configNode = document.getElementById('camera-guides');
    var config = {ratio: [3, 4], positions: {}};
    try { if (configNode) config = JSON.parse(configNode.textContent); } catch (e) { /* keep the defaults */ }
    var RATIO = config.ratio[0] / config.ratio[1];

    // Remembered per device (each wrapped: private windows and blocked storage must not break the camera)
    function remember(key, value) { try { localStorage.setItem('mt-cam-' + key, String(value)); } catch (e) { /* ignore */ } }
    function recall(key, fallback) { try { var v = localStorage.getItem('mt-cam-' + key); return v === null ? fallback : v; } catch (e) { return fallback; } }
    var prefs = {guides: recall('guides', '1') === '1', ghost: recall('ghost', '1') === '1', timer: parseInt(recall('timer', '0'), 10) || 0};
    facing = recall('facing', 'environment') === 'user' ? 'user' : 'environment';

    function say(text) { note.textContent = text; note.hidden = !text; }

    function setFile(file) {
        try {
            var transfer = new DataTransfer();
            transfer.items.add(file);
            fileInput.files = transfer.files;
        } catch (e) { return false; }
        var img = picked.querySelector('img');
        if (img.dataset.url) URL.revokeObjectURL(img.dataset.url);
        img.dataset.url = URL.createObjectURL(file);
        img.src = img.dataset.url;
        picked.hidden = false;
        return true;
    }

    // Shrink and re-encode as JPEG; if the browser cannot read the file (e.g. HEIC) the original goes as it is
    function shrink(blob, name) {
        return new Promise(function (resolve) {
            var url = URL.createObjectURL(blob);
            var img = new Image();
            img.onload = function () {
                var scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
                var canvas = document.createElement('canvas');
                canvas.width = Math.round(img.naturalWidth * scale);
                canvas.height = Math.round(img.naturalHeight * scale);
                canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
                URL.revokeObjectURL(url);
                canvas.toBlob(function (out) {
                    resolve(out ? new File([out], name.replace(/\.\w+$/, '') + '.jpg', {type: 'image/jpeg'}) : blob);
                }, 'image/jpeg', 0.88);
            };
            img.onerror = function () { URL.revokeObjectURL(url); resolve(blob); };
            img.src = url;
        });
    }

    function take(file) {
        shrink(file, file.name || 'foto.jpg').then(function (small) {
            if (!(small instanceof File)) small = new File([small], file.name || 'foto', {type: small.type});
            setFile(small);
        });
    }

    // ---- live camera with guides ----------------------------------------------------------------------------------

    function current() {
        return config.positions[angleSelect ? angleSelect.value : ''] || {label: '', guide: '', ghosts: []};
    }

    // the previous photo of this position, leaving out the one of the assessment being photographed (it will be replaced)
    function ghostFor(position) {
        var chosen = assessmentSelect && assessmentSelect.value ? parseInt(assessmentSelect.value, 10) : null;
        for (var i = 0; i < position.ghosts.length; i++) {
            if (chosen === null || position.ghosts[i].assessment !== chosen) return position.ghosts[i];
        }
        return null;
    }

    function layout() {   // the biggest 3:4 frame that fits the space between the bars
        var w = stage.clientWidth, h = stage.clientHeight;
        var height = Math.min(h, w / RATIO);
        frame.style.height = Math.floor(height) + 'px';
        frame.style.width = Math.floor(height * RATIO) + 'px';
    }

    function drawGuides() {
        var position = current();
        guidesBox.replaceChildren();
        guidesBox.hidden = !prefs.guides;
        if (window.PhotoGuides) guidesBox.appendChild(window.PhotoGuides.svg(position.guide));
        live.querySelector('[data-toggle-guides]').setAttribute('aria-pressed', prefs.guides ? 'true' : 'false');
        var ghost = ghostFor(position);
        var ghostBtn = live.querySelector('[data-toggle-ghost]');
        ghostBtn.hidden = !ghost;
        ghostBtn.setAttribute('aria-pressed', prefs.ghost ? 'true' : 'false');
        if (ghost) {
            ghostImg.src = ghost.url;
            ghostImg.hidden = !prefs.ghost;
            var when = new Date(ghost.date + 'T00:00:00');
            live.querySelector('[data-ghost-label]').textContent = live.dataset.textPrevious + ' · ' +
                (isNaN(when) ? ghost.date : when.toLocaleDateString(document.documentElement.lang || undefined));
        } else {
            ghostImg.hidden = true;
            ghostImg.removeAttribute('src');
        }
        live.querySelector('[data-cam-position]').textContent = position.label;
        live.querySelector('[data-cam-note]').textContent = '';
        live.querySelector('[data-tip]').textContent = position.guide ? live.dataset.textGuidesTip : live.dataset.textFreeTip;
    }

    function drawTimer() {
        live.querySelector('[data-timer-label]').textContent = prefs.timer ? prefs.timer + ' s' : live.dataset.textNoTimer;
        live.querySelector('[data-timer]').classList.toggle('on', !!prefs.timer);
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
    }

    function openStream() {
        if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
        var constraints = {video: {facingMode: {ideal: facing}, width: {ideal: 1440}, height: {ideal: 1920}}, audio: false};
        return navigator.mediaDevices.getUserMedia(constraints).then(function (s) {
            stream = s;
            video.srcObject = s;
            frame.classList.toggle('mirror', facing === 'user');   // a selfie preview is mirrored; the saved photo is not
            return video.play();
        });
    }

    function startLive() {
        say('');
        live.hidden = false;
        document.documentElement.classList.add('cam-open');
        drawGuides();
        drawTimer();
        layout();
        window.addEventListener('resize', layout);
        openStream().then(function () {
            layout();
            if (navigator.mediaDevices.enumerateDevices) {
                navigator.mediaDevices.enumerateDevices().then(function (devices) {
                    live.querySelector('[data-flip]').hidden = devices.filter(function (d) { return d.kind === 'videoinput'; }).length < 2;
                }).catch(function () { /* the flip button just stays hidden */ });
            }
        }).catch(function () {
            stopLive();
            if (coarse) {   // permission refused or camera busy: the phone's own camera app still works
                say(gettext('Não foi possível usar a câmera com guias. Abrindo a câmera do aparelho.'));
                phoneCamera.click();
            } else {
                say(gettext('Não foi possível usar a câmera (permissão negada ou câmera ocupada). Escolha a foto da galeria.'));
            }
        });
    }

    // The frame is cropped from the video exactly as it is shown (object-fit: cover), so the photo matches what was framed
    function shoot() {
        var vw = video.videoWidth, vh = video.videoHeight;
        if (!vw || !vh) return;
        var sw = vw, sh = vh;
        if (vw / vh > RATIO) { sw = vh * RATIO; } else { sh = vw / RATIO; }
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
            if (blob) setFile(new File([blob], 'foto.jpg', {type: 'image/jpeg'}));
            stopLive();
        }, 'image/jpeg', 0.88);
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
    live.querySelector('[data-cancel-live]').addEventListener('click', stopLive);
    document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && !live.hidden) stopLive(); });
    live.querySelector('[data-flip]').addEventListener('click', function () {
        facing = facing === 'user' ? 'environment' : 'user';
        remember('facing', facing);
        openStream().catch(function () { say(gettext('Não foi possível trocar de câmera.')); });
    });
    live.querySelector('[data-toggle-guides]').addEventListener('click', function () {
        prefs.guides = !prefs.guides;
        remember('guides', prefs.guides ? '1' : '0');
        drawGuides();
    });
    live.querySelector('[data-toggle-ghost]').addEventListener('click', function () {
        prefs.ghost = !prefs.ghost;
        remember('ghost', prefs.ghost ? '1' : '0');
        drawGuides();
    });
    live.querySelector('[data-timer]').addEventListener('click', function () {
        prefs.timer = prefs.timer === 0 ? 5 : (prefs.timer === 5 ? 10 : 0);
        remember('timer', prefs.timer);
        drawTimer();
    });
    window.addEventListener('pagehide', stopLive);

    // ---- the form around it -----------------------------------------------------------------------------------------

    var coarse = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
    var canLive = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.isSecureContext);

    function ready(hasCamera) {
        buttons.hidden = false;
        fileInput.classList.add('d-none');   // the buttons replace the raw file field
        fileInput.required = false;
        form.addEventListener('submit', function (event) {
            if (!fileInput.files.length) {
                event.preventDefault();
                say(gettext('Tire ou escolha uma foto antes de salvar.'));
            }
        });
        var takeBtn = buttons.querySelector('[data-take]');
        var nativeBtn = buttons.querySelector('[data-native-camera]');
        buttons.querySelector('[data-gallery]').addEventListener('click', function () { stopLive(); fileInput.click(); });
        nativeBtn.addEventListener('click', function () { phoneCamera.click(); });
        if (!hasCamera) {
            takeBtn.hidden = true;
            say(gettext('Nenhuma câmera encontrada neste aparelho: escolha a foto da galeria ou dos arquivos.'));
            return;
        }
        // with the live camera available the native app is only an alternative; without it (plain http) it is the camera
        nativeBtn.hidden = !(coarse && canLive);
        takeBtn.addEventListener('click', function () {
            if (canLive) { startLive(); } else { phoneCamera.click(); }
        });
    }

    phoneCamera.addEventListener('change', function () { if (phoneCamera.files[0]) take(phoneCamera.files[0]); });
    fileInput.addEventListener('change', function () {
        if (fileInput.files[0] && !fileInput.dataset.busy) {
            var file = fileInput.files[0];
            fileInput.dataset.busy = '1';
            shrink(file, file.name).then(function (small) {
                if (small instanceof File) setFile(small); else setFile(file);
                delete fileInput.dataset.busy;
            });
        }
    });

    // The date only matters for loose photos; "save and next" only makes sense inside an assessment
    function syncAssessment() {
        var has = !!assessmentSelect.value;
        form.querySelector('[data-loose-date]').hidden = has;
        form.querySelector('[data-save-next]').hidden = !has;
    }
    if (assessmentSelect) assessmentSelect.addEventListener('change', syncAssessment);

    if (coarse) {
        ready(true);   // a phone/tablet always has a camera app; without one the same input opens the gallery
    } else if (canLive && navigator.mediaDevices.enumerateDevices) {
        navigator.mediaDevices.enumerateDevices().then(function (devices) {
            ready(devices.some(function (d) { return d.kind === 'videoinput'; }));
        }).catch(function () { ready(false); });
    } else {
        ready(false);
    }
})();
