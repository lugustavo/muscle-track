/* Framing guides drawn over the live camera (SVG in a 300 x 400 box, the 3:4 portrait frame): the rule-of-thirds grid,
   the centre line, level lines (head, shoulders, hips, floor) and, per position, a neutral dashed silhouette.
   `PhotoGuides.svg(guide, {grid, levels})` returns the markup; guide = front | back | side-left | side-right | ''.
   Every line is drawn twice (a dark one under a light one) so it reads over both bright and dark backgrounds. */
(function () {
    var NS = 'http://www.w3.org/2000/svg';

    // left half of the front/back view (mirrored for the right half): head, neck, shoulder, arm, torso, leg
    var HALF = 'M139,66 L137,80 Q126,84 112,90 Q103,96 101,114 L93,178 Q90,196 89,208 L100,211 Q104,196 107,178 ' +
        'L113,132 Q113,150 117,176 Q116,196 118,214 L120,290 Q121,330 122,364 L124,368 L147,368 L147,330 Q149,290 150,236';
    // side view looking to the right: back line, heel, foot, front of the leg, belly, chest, neck; then the arm
    var SIDE = 'M142,64 L141,80 Q134,90 133,110 Q131,140 135,165 Q137,185 136,205 Q135,215 138,230 L140,290 L141,360 ' +
        'L141,368 L172,368 L172,360 Q158,358 158,350 L156,290 L158,240 Q166,222 162,205 Q170,190 166,168 Q172,140 168,112 ' +
        'Q164,92 156,84 L153,66';
    var SIDE_ARM = 'M150,90 Q154,130 156,170 L162,206';

    function el(name, attrs) {
        var node = document.createElementNS(NS, name);
        Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
        return node;
    }

    // a line/shape twice: the dark base first, the light stroke on top
    function both(parent, make) {
        ['pg-shadow', 'pg-line'].forEach(function (cls) {
            var node = make();
            node.setAttribute('class', (node.getAttribute('class') || '') + ' ' + cls);
            parent.appendChild(node);
        });
    }

    function line(parent, x1, y1, x2, y2, extra) {
        both(parent, function () { return el('line', Object.assign({x1: x1, y1: y1, x2: x2, y2: y2}, extra || {})); });
    }

    function path(parent, d, extra) {
        both(parent, function () { return el('path', Object.assign({d: d}, extra || {})); });
    }

    function silhouette(parent, guide) {
        var g = el('g', {'class': 'pg-body'});
        if (guide === 'front' || guide === 'back') {
            both(g, function () { return el('circle', {cx: 150, cy: 46, r: 19}); });
            path(g, HALF);
            path(g, HALF, {transform: 'translate(300,0) scale(-1,1)'});
            if (guide === 'back') line(g, 150, 68, 150, 214, {'class': 'pg-dash'});   // the spine
        } else if (guide === 'side-left' || guide === 'side-right') {
            var side = el('g', guide === 'side-left' ? {transform: 'translate(300,0) scale(-1,1)'} : {});
            both(side, function () { return el('circle', {cx: 152, cy: 46, r: 18}); });
            path(side, SIDE);
            path(side, SIDE_ARM);
            g.appendChild(side);
        }
        parent.appendChild(g);
    }

    function svg(guide, opts) {
        opts = opts || {};
        var root = el('svg', {viewBox: '0 0 300 400', preserveAspectRatio: 'none', 'aria-hidden': 'true', focusable: 'false'});
        if (opts.grid !== false) {
            var grid = el('g', {'class': 'pg-grid'});
            [100, 200].forEach(function (x) { line(grid, x, 0, x, 400); });
            [133.3, 266.7].forEach(function (y) { line(grid, 0, y, 300, y); });
            root.appendChild(grid);
        }
        if (opts.levels !== false) {
            var levels = el('g', {'class': 'pg-levels'});
            line(levels, 150, 0, 150, 400, {'class': 'pg-dash'});                  // centre
            [[26, 'head'], [96, 'shoulders'], [206, 'hips'], [368, 'floor']].forEach(function (row) {
                line(levels, 0, row[0], 300, row[0], {'class': 'pg-level pg-' + row[1]});
            });
            root.appendChild(levels);
        }
        if (guide) silhouette(root, guide);
        return root;
    }

    window.PhotoGuides = {svg: svg};
})();
