/* Filters of the list pages: typing in the search box and picking a chip refresh the results in place (no reload,
   the box keeps focus); without JavaScript the form still submits normally. The layout toggle and the filter tags are
   plain links inside the results region. */
(function () {
    var form = document.querySelector('[data-list-filters]');
    var region = document.querySelector('[data-list-region]');
    if (!form || !region || !window.fetch || !window.URLSearchParams) return;

    var search = form.querySelector('input[data-search]');
    var timer = null;
    var controller = null;

    function address() {
        var params = new URLSearchParams(new FormData(form));
        Array.from(params.keys()).forEach(function (key) { if (!params.get(key)) params.delete(key); });
        var query = params.toString();
        return form.getAttribute('action') + (query ? '?' + query : '');
    }

    function refresh() {
        clearTimeout(timer);
        if (controller) controller.abort();
        controller = window.AbortController ? new AbortController() : null;
        var target = address();
        region.classList.add('is-loading');
        fetch(target, { credentials: 'same-origin', signal: controller ? controller.signal : undefined })
            .then(function (response) {
                if (!response.ok) throw new Error('status ' + response.status);
                return response.text();
            })
            .then(function (html) {
                var next = new DOMParser().parseFromString(html, 'text/html').querySelector('[data-list-region]');
                if (!next) throw new Error('no region');
                region.innerHTML = next.innerHTML;
                region.classList.remove('is-loading');
                history.replaceState(null, '', target);
            })
            .catch(function (error) {
                if (error && error.name === 'AbortError') return;
                window.location.href = target;
            });
    }

    form.addEventListener('submit', function (event) { event.preventDefault(); refresh(); });
    form.addEventListener('change', function (event) {
        if (event.target.type === 'radio' || event.target.tagName === 'SELECT') refresh();
    });
    if (search) {
        search.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(refresh, 350);
        });
    }
    var clear = form.querySelector('[data-search-clear]');
    if (clear && search) {
        clear.addEventListener('click', function () { search.value = ''; search.focus(); refresh(); });
    }
})();
