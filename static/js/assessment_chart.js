/* Evolution chart of the assessments (weight, body fat, waist). Data: json_script #assessment-chart-data. */
(function () {
    var dataEl = document.getElementById('assessment-chart-data');
    if (dataEl) {
        var points = JSON.parse(dataEl.textContent);
        new Chart(document.getElementById('assessmentChart'), {
            type: 'line',
            data: {
                labels: points.map(function (p) { return p.date; }),
                datasets: [
                    {label: 'Peso (kg)', data: points.map(function (p) { return p.weight; }), borderColor: '#FF6B6B', yAxisID: 'y', spanGaps: true, tension: 0.25},
                    {label: '% gordura', data: points.map(function (p) { return p.body_fat; }), borderColor: '#4ECDC4', borderDash: [6, 4], yAxisID: 'y1', spanGaps: true, tension: 0.25},
                    {label: 'Cintura (cm)', data: points.map(function (p) { return p.waist; }), borderColor: MTChartColor('#2C3E50', '#9db4cc'), mtColors: ['#2C3E50', '#9db4cc'], yAxisID: 'y', spanGaps: true, tension: 0.25, hidden: true}
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: {
                    y: {position: 'left', title: {display: true, text: 'kg / cm'}},
                    y1: {position: 'right', grid: {drawOnChartArea: false}, title: {display: true, text: '%'}}
                }
            }
        });
    }
})();
