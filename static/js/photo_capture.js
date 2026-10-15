/* Photo form: take the picture with the device camera when there is one, otherwise pick it from the gallery.
   - phones/tablets: a file input with `capture` opens the native camera app (without a camera it opens the gallery);
   - computers with a camera: live preview (getUserMedia) with a "Capturar" button;
   - no camera, no permission or no support: the plain file field (gallery/files) with a short explanation.
   The picture is shrunk in the browser (phone photos pass 5 MB) before it is sent. */
(function () {
    var form = document.querySelector('[data-photo-form]');
    if (!form) return;
    var fileInput = form.querySelector('input[type=file]:not([data-phone-camera])');
    var phoneCamera = form.querySelector('[data-phone-camera]');
    var buttons = form.querySelector('[data-capture-buttons]');
    var live = form.querySelector('[data-live]');
    var video = live.querySelector('video');
    var picked = form.querySelector('[data-picked]');
    var note = form.querySelector('[data-capture-note]');
    var stream = null;
    var MAX_SIDE = 1800;

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

    function stopLive() {
        if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
        stream = null;
        video.srcObject = null;
        live.hidden = true;
    }

    function startLive() {
        say('');
        navigator.mediaDevices.getUserMedia({video: {facingMode: 'environment'}, audio: false}).then(function (s) {
            stream = s;
            video.srcObject = s;
            video.play();
            live.hidden = false;
        }).catch(function () {
            say('Não foi possível usar a câmera (permissão negada ou câmera ocupada). Escolha a foto da galeria.');
        });
    }

    var coarse = window.matchMedia && window.matchMedia('(pointer: coarse)').matches;
    var canLive = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.isSecureContext);

    function ready(hasCamera) {
        buttons.hidden = false;
        fileInput.classList.add('d-none');   // the buttons replace the raw file field
        fileInput.required = false;
        form.addEventListener('submit', function (event) {
            if (!fileInput.files.length) {
                event.preventDefault();
                say('Tire ou escolha uma foto antes de salvar.');
            }
        });
        var takeBtn = buttons.querySelector('[data-take]');
        buttons.querySelector('[data-gallery]').addEventListener('click', function () { stopLive(); fileInput.click(); });
        if (!hasCamera) {
            takeBtn.hidden = true;
            say('Nenhuma câmera encontrada neste aparelho: escolha a foto da galeria ou dos arquivos.');
            return;
        }
        takeBtn.addEventListener('click', function () {
            if (coarse) { phoneCamera.click(); } else { startLive(); }
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

    live.querySelector('[data-shoot]').addEventListener('click', function () {
        var canvas = document.createElement('canvas');
        var scale = Math.min(1, MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
        canvas.width = Math.round(video.videoWidth * scale);
        canvas.height = Math.round(video.videoHeight * scale);
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) {
            if (blob) setFile(new File([blob], 'foto.jpg', {type: 'image/jpeg'}));
            stopLive();
        }, 'image/jpeg', 0.88);
    });
    live.querySelector('[data-cancel-live]').addEventListener('click', stopLive);
    window.addEventListener('pagehide', stopLive);

    // The date only matters for loose photos; "save and next" only makes sense inside an assessment
    var assessment = form.querySelector('select[name=assessment]');
    function syncAssessment() {
        var has = !!assessment.value;
        form.querySelector('[data-loose-date]').hidden = has;
        form.querySelector('[data-save-next]').hidden = !has;
    }
    assessment.addEventListener('change', syncAssessment);

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
