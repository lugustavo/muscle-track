// Chart.js follows the color theme: text and grid colors come from the CSS variables, and charts already on the page update on a change.
(function () {
    'use strict';
    if (typeof Chart === 'undefined') { return; }

    function css(name, fallback) {
        var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return value || fallback;
    }

    function paint() {
        Chart.defaults.color = css('--bs-secondary-color', '#6c757d');
        Chart.defaults.borderColor = css('--bs-border-color', '#dee2e6');
    }

    // A series that is dark navy in the light theme needs a lighter shade on dark backgrounds
    window.MTChartColor = function (light, dark) {
        return document.documentElement.getAttribute('data-bs-theme') === 'dark' ? dark : light;
    };

    paint();
    document.addEventListener('mt-theme-change', function () {
        paint();
        Object.keys(Chart.instances).forEach(function (id) {
            var chart = Chart.instances[id];
            var scales = chart.options.scales || {};
            Object.keys(scales).forEach(function (key) {
                var scale = scales[key];
                scale.grid = Object.assign({}, scale.grid, {color: Chart.defaults.borderColor});
                scale.ticks = Object.assign({}, scale.ticks, {color: Chart.defaults.color});
                if (scale.title) { scale.title.color = Chart.defaults.color; }
            });
            if (chart.options.plugins && chart.options.plugins.legend) {
                chart.options.plugins.legend.labels = Object.assign({}, chart.options.plugins.legend.labels, {color: Chart.defaults.color});
            }
            (chart.data.datasets || []).forEach(function (ds) {
                if (ds.mtColors) {
                    ds.borderColor = window.MTChartColor(ds.mtColors[0], ds.mtColors[1]);
                }
            });
            chart.update();
        });
    });
})();
