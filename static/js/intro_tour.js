/* First-access tour on the student's "Hoje" screen: three steps in a modal. Finishing or skipping tells the server
   (POST to the tour address) so it is not shown again; "Como usar o app" in the profile shows it any time. */
function startIntroTour() {
    var gettext = window.gettext || function (s) { return s; };   // Django's JS catalog (/jsi18n/)
    var el = document.getElementById('introTour');
    if (!el || !window.bootstrap) return;
    var steps = Array.prototype.slice.call(el.querySelectorAll('[data-intro-step]'));
    var dots = Array.prototype.slice.call(el.querySelectorAll('[data-intro-dot]'));
    var back = el.querySelector('[data-intro-back]');
    var next = el.querySelector('[data-intro-next]');
    var index = 0;
    var modal = bootstrap.Modal.getOrCreateInstance(el);

    function show(i) {
        index = i;
        steps.forEach(function (s, n) { s.hidden = n !== i; });
        dots.forEach(function (d, n) { d.className = 'rounded-circle ' + (n === i ? 'bg-primary' : 'bg-secondary-subtle'); });
        back.style.visibility = i === 0 ? 'hidden' : 'visible';
        next.textContent = i === steps.length - 1 ? gettext('Entendi') : gettext('Próximo');
    }

    function done() {
        var token = el.querySelector('[name=csrfmiddlewaretoken]');
        try {
            fetch(el.getAttribute('data-intro-url'), {
                method: 'POST', credentials: 'same-origin',
                headers: {'X-CSRFToken': token ? token.value : '', 'X-Requested-With': 'fetch'}
            });
        } catch (e) { /* the tour simply shows again next time */ }
        modal.hide();
    }

    next.addEventListener('click', function () { if (index < steps.length - 1) show(index + 1); else done(); });
    back.addEventListener('click', function () { if (index > 0) show(index - 1); });
    el.querySelector('[data-intro-skip]').addEventListener('click', done);
    show(0);
    modal.show();
}

// Bootstrap is loaded at the end of the page: start once everything is there
if (document.readyState === 'complete') startIntroTour();
else window.addEventListener('load', startIntroTour);
