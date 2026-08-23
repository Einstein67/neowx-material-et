// v1.0.1
// Tooltip support
$(function () {
    $('[data-toggle="tooltip"]').tooltip()
});

// Number rounding based on weewx values
// Example: Number: 34.5678 Format: %.2f Result: 34.57
function formatNumber(no, format) {
    // Guard non-numeric values (null/undefined for hidden series or null data
    // gaps): calling .toFixed on them throws and aborts the whole tooltip
    // render, so the tooltip vanishes after toggling a series off the legend.
    if (typeof no !== 'number' || !isFinite(no)) {
        return no;
    }
    // Extract number of decimal places from format
    format = format.replace(/[^0-9]/g, '');
    return no.toFixed(format);
}

// --- HELPER FUNCTION: Debug logging (respects debug flag) ---
function debugLog(message) {
    if (window.MQTT_CONFIG && window.MQTT_CONFIG.debug) {
        console.log('[MQTT DEBUG] ' + message);
    }
}

document.addEventListener('DOMContentLoaded', function () {
    // Get all IDs of elements with class "card-value" and store in a variable
    const valueCards = document.querySelectorAll('.card-value');
    const valueCardIds = Array.from(valueCards).map(card => card.id);
    debugLog('Value Card IDs: ' + valueCardIds);

    // Get all IDs of elements with class "card-chart" and store in a variable
    const cardCharts = document.querySelectorAll('.card-chart');
    const cardChartIds = Array.from(cardCharts).map(card => card.id);
    debugLog('Card Chart IDs: ' + cardChartIds);

    // --- CLICK HANDLER 1: Value Cards -> Jump to Chart Cards ---
    valueCards.forEach(function(valueCard) {
        valueCard.addEventListener('click', function(e) {
            // Get the sensor name from data-name attribute
            const sensorName = valueCard.getAttribute('data-name');
            if (!sensorName) {
                debugLog('⚠️ No data-name found on value card');
                return;
            }

            // Special handling for wind icon - jump to wind vector image
            if (sensorName === 'windSpeed' && e.target.closest('i.wi')) {
                const vectorChart = document.getElementById('embedded_imageWindVect');
                if (vectorChart) {
                    scrollToElement(vectorChart);
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                }
            }

            // Don't navigate if clicking on icons (except wind)
            if (e.target.closest('i.wi')) {
                e.preventDefault();
                e.stopPropagation();
                return;
            }

            // Map sensor to chart name
            let chartSensorName = sensorName;

            // Special mappings: some sensors share the same chart
            if (sensorName === 'appTemp' || sensorName === 'dewpoint') {
                chartSensorName = 'outTemp';
            } else if (sensorName === 'heatindex') {
                chartSensorName = 'windchill';
            }

            // Find the corresponding chart card by data-name (direct match first)
            let chartCard = document.querySelector('.card-chart[data-name="' + chartSensorName + '"]');

            // Fallback: search custom charts whose data-sensors attribute contains this sensor
            if (!chartCard) {
                const customCharts = document.querySelectorAll('.card-chart[data-sensors]');
                for (const cc of customCharts) {
                    const sensors = cc.getAttribute('data-sensors').split(',').map(function(s) { return s.trim(); });
                    if (sensors.indexOf(chartSensorName) !== -1) {
                        chartCard = cc;
                        break;
                    }
                }
            }

            if (chartCard) {
                scrollToElement(chartCard, true);
                debugLog('✓ Jumped from value card "' + sensorName + '" to chart "' + chartCard.getAttribute('data-name') + '"');
            } else {
                debugLog('⚠️ No chart card found for sensor: ' + chartSensorName);
            }
        });
    });

    // --- CLICK HANDLER 2: Chart Cards -> Jump to Value Cards ---
    cardCharts.forEach(function(chartCard) {
        chartCard.addEventListener('click', function(e) {
            // Only respond to clicks on the title (H5 element)
            if (e.target.tagName !== 'H5') {
                return;
            }

            // Get the sensor name from data-name attribute
            const sensorName = chartCard.getAttribute('data-name');
            if (!sensorName) {
                debugLog('⚠️ No data-name found on chart card');
                return;
            }

            // Map chart name to value card name
            let valueSensorName = sensorName;

            // Special mappings
            if (sensorName === 'windvec') {
                valueSensorName = 'windSpeed';
            }

            // Find the corresponding value card by data-name (direct match first)
            let valueCard = document.querySelector('.card-value[data-name="' + valueSensorName + '"]');

            // Fallback: if this is a custom chart, use data-sensors to find first existing value card
            if (!valueCard) {
                const dataSensors = chartCard.getAttribute('data-sensors');
                if (dataSensors) {
                    const sensors = dataSensors.split(',').map(function(s) { return s.trim(); });
                    for (const sensor of sensors) {
                        const candidate = document.querySelector('.card-value[data-name="' + sensor + '"]');
                        if (candidate) {
                            valueCard = candidate;
                            break;
                        }
                    }
                }
            }

            if (valueCard) {
                scrollToElement(valueCard, true);
                debugLog('✓ Jumped from chart "' + sensorName + '" to value card "' + valueCard.getAttribute('data-name') + '"');
                e.preventDefault();
            } else {
                debugLog('⚠️ No value card found for sensor: ' + valueSensorName);
            }
        });
    });

    // --- HELPER FUNCTION: Smooth scroll to element with visual feedback ---
    function scrollToElement(element, highlightBackground) {
        if (!element) return;

        const topOffset = element.getBoundingClientRect().top + window.pageYOffset - 100;
        window.scrollTo({
            top: topOffset,
            behavior: 'smooth'
        });

        // Optional green background flash
        if (highlightBackground) {
            element.style.transition = "background-color 0.5s";
            element.style.backgroundColor = "rgba(0, 255, 0, 0.1)";
            setTimeout(function() {
                element.style.backgroundColor = "";
            }, 1000);
        }
    }
});

// --- POP-UP WINDOW --------------------------------------------------------
// One shared modal, used by two things:
//
//   - charts, through an ApexCharts toolbar customIcon registered in js.inc
//     when Appearance/enableChartPopup is on (ApexCharts 5 binds the click as
//     click(chartCtx, w, event));
//   - [[Embedded]] iFrame and image cards, through the toolbar button the
//     templates emit when Appearance/enableEmbeddedPopup is on.

var NEOWX_POPUP_ID = 'neowx-popup';

// There is one modal, one mount point and one _neowxPopupChart slot, so only
// one pop-up can be open at a time. Every path that sets this must also clear
// it: a guard left stuck on makes every expand button on the page stop
// responding, with nothing on screen or in the console to explain why.
var _neowxPopupActive = false;
var _neowxPopupTeardown = null;

// Runs the pending teardown once. A teardown that throws must not abort the
// rest of the close: ApexCharts' destroy() reaches into state that only exists
// once render() has finished, and letting that escape would strand the guard.
function neowxRunTeardown() {
    var teardown = _neowxPopupTeardown;
    _neowxPopupTeardown = null;
    if (!teardown) {
        return;
    }
    try {
        teardown();
    } catch (e) {
        console.error('neowx-material: closing the pop-up window failed', e);
    }
}

// Builds the modal on first use and returns it on every call.
function neowxPopupModal() {
    var existing = document.getElementById(NEOWX_POPUP_ID);
    if (existing) {
        return existing;
    }

    var wrapper = document.createElement('div');
    wrapper.innerHTML = [
        '<div class="modal fade" id="' + NEOWX_POPUP_ID + '" tabindex="-1" role="dialog"',
        ' aria-labelledby="' + NEOWX_POPUP_ID + '-title" aria-hidden="true">',
        '<div class="modal-dialog modal-xl modal-dialog-centered" role="document">',
        '<div class="modal-content">',
        '<div class="modal-header">',
        '<h5 class="modal-title" id="' + NEOWX_POPUP_ID + '-title"></h5>',
        '<button type="button" class="close" data-dismiss="modal">',
        '<span aria-hidden="true">&times;</span>',
        '</button>',
        '</div>',
        '<div class="modal-body">',
        '<div id="' + NEOWX_POPUP_ID + '-mount"></div>',
        '</div>',
        '</div>',
        '</div>',
        '</div>'
    ].join('');
    var modal = wrapper.firstElementChild;
    var closeLabel = (window.NEOWX_TEXTS && window.NEOWX_TEXTS.close) || 'Close';
    modal.querySelector('.close').setAttribute('aria-label', closeLabel);
    document.body.appendChild(modal);

    // Bootstrap already closes on ESC and on a click outside the dialog, so all
    // that is left here is emptying the dialog out again.
    $(modal).on('hidden.bs.modal', function () {
        // Bootstrap 4 ignores hide() while the dialog is still fading in, so a
        // pending shown handler should never outlive a close. Drop it anyway
        // rather than let that behaviour be load-bearing.
        $(modal).off('shown.bs.modal.neowx');
        neowxRunTeardown();
        var mount = document.getElementById(NEOWX_POPUP_ID + '-mount');
        if (mount) {
            mount.innerHTML = '';
        }
        _neowxPopupActive = false;
    });

    return modal;
}

// onShown is handed the (empty) mount point once the dialog is on screen, and
// onHidden - if given - runs when it closes, before the mount is emptied.
function neowxOpenPopup(title, onShown, onHidden) {
    if (_neowxPopupActive) {
        return;
    }
    _neowxPopupActive = true;
    _neowxPopupTeardown = onHidden || null;

    try {
        var modal = neowxPopupModal();
        document.getElementById(NEOWX_POPUP_ID + '-title').textContent = title;

        // Fill the dialog only once it is on screen. Nothing inside a modal has
        // a size while Bootstrap still has it at display:none, and with .fade
        // that is not swapped inside modal('show') - the backdrop transition
        // runs first - so a chart or an iframe would measure its container as
        // zero and render collapsed.
        $(modal).one('shown.bs.modal.neowx', function () {
            try {
                onShown(document.getElementById(NEOWX_POPUP_ID + '-mount'));
            } catch (e) {
                console.error('neowx-material: could not fill the pop-up window', e);
            }
        });
        $(modal).modal('show');
    } catch (e) {
        // The dialog never opened, so hidden.bs.modal will not fire to release
        // the guard.
        _neowxPopupActive = false;
        _neowxPopupTeardown = null;
        console.error('neowx-material: could not open the pop-up window', e);
    }
}

// The card heading is the only title either kind of pop-up has to work with.
function neowxCardTitle(el) {
    var card = el && el.closest ? el.closest('.card') : null;
    var heading = card ? card.querySelector('h5') : null;
    return heading ? heading.textContent.trim() : '';
}

// --- Charts ---------------------------------------------------------------

var _neowxPopupChart = null;

// Deep copy that keeps functions (axis formatters, chart event handlers) by
// reference. A JSON round-trip would drop them, and handing the new chart the
// source chart's own sub-objects would let ApexCharts mutate the card chart
// while normalising the copy.
function neowxCloneChartConfig(value, seen) {
    if (value === null || typeof value !== 'object') {
        return value;
    }
    if (value instanceof Date) {
        return new Date(value.getTime());
    }
    seen = seen || new WeakMap();
    if (seen.has(value)) {
        return seen.get(value);
    }
    var copy = Array.isArray(value) ? [] : {};
    seen.set(value, copy);
    for (var key in value) {
        if (Object.prototype.hasOwnProperty.call(value, key)) {
            copy[key] = neowxCloneChartConfig(value[key], seen);
        }
    }
    return copy;
}

// The chart config carries no title, so reuse the card heading. Every chart in
// the skin sits in a .card with an <h5>, telemetry included, so the series-name
// path below is a safety net for a chart mounted outside one.
function neowxPopupTitle(chartCtx) {
    var heading = neowxCardTitle(chartCtx.el);
    if (heading !== '') {
        return heading;
    }
    var series = chartCtx.w && chartCtx.w.config ? chartCtx.w.config.series : null;
    var names = Array.isArray(series) ? series.map(function (s) {
        return s && s.name;
    }).filter(Boolean) : [];
    return names.length > 0 ? names.join(' & ') : '';
}

function neowxChartPopup(chartCtx) {
    var config = neowxCloneChartConfig(chartCtx.w.config);
    // height '100%' measures the mount point's PARENT, the modal body, which
    // the stylesheet gives a viewport-relative height. width '100%' measures
    // the mount itself.
    config.chart.height = '100%';
    config.chart.width = '100%';
    // No chart in the skin sets either today. If one ever did, a duplicate id
    // would make ApexCharts.exec() resolve to whichever instance registered
    // first - the card chart, not the copy - and a duplicate group would pull
    // the copy into the card chart's zoom and hover syncing.
    delete config.chart.id;
    delete config.chart.group;
    // No expand button on the chart that is already expanded.
    if (config.chart.toolbar && config.chart.toolbar.tools) {
        config.chart.toolbar.tools.customIcons = [];
    }

    neowxOpenPopup(neowxPopupTitle(chartCtx), function (mount) {
        // The copy inherits chart.events.mounted by reference. On a customChart*
        // using hidevalues that is the handler chart_hidden_series.inc builds,
        // so the copy re-hides those series even when they were un-hidden on
        // the card.
        _neowxPopupChart = new ApexCharts(mount, config);
        var rendering = _neowxPopupChart.render();
        if (rendering && rendering.then) {
            rendering.then(null, function (e) {
                console.error('neowx-material: the expanded chart could not be drawn', e);
            });
        }
    }, function () {
        var chart = _neowxPopupChart;
        _neowxPopupChart = null;
        if (!chart) {
            return;
        }
        // NEOWX_ALIGNED lives in js.inc and is filled by neowxMountedHandler,
        // which the copy inherits along with the cloned config. Without this an
        // align-mode chart leaves an entry behind on every open.
        if (typeof NEOWX_ALIGNED !== 'undefined' && chart.w && chart.w.globals) {
            delete NEOWX_ALIGNED[chart.w.globals.chartID];
        }
        chart.destroy();
    });
}

// --- Embedded iFrames and images ------------------------------------------

// Copies the card's own iframe/img rather than rebuilding one from the config,
// so the src (including the cache-busting timestamp on images) and the alt text
// come along for free. Any link around the image is left on the card.
function neowxEmbedPopup(button) {
    var card = button.closest('.card');
    // Scoped to the card body so a decorative image added to a card header
    // later would not be expanded in place of the embed itself.
    var media = card ? card.querySelector('.card-body iframe, .card-body img') : null;
    if (!media) {
        // The button is visible and focusable, so returning quietly here would
        // leave a dead control with nothing to diagnose it by.
        console.error('neowx-material: expand button found no iframe or image to ' +
            'show - has the card markup changed?', button);
        return;
    }

    var copy = media.cloneNode(true);
    // On the card the media is a flex child pinned to a fixed aspect ratio; in
    // the dialog the stylesheet sizes it instead.
    copy.classList.remove('flex-grow-1', 'w-100');
    copy.classList.add('nwm-embed-popup-media');
    copy.style.removeProperty('aspect-ratio');

    var title = neowxCardTitle(button) || media.getAttribute('alt') || '';
    neowxOpenPopup(title, function (mount) {
        mount.appendChild(copy);
    });
}

// Registered unconditionally: app.js is a plain .js file with no access to the
// skin config, and with enableEmbeddedPopup off the templates emit no buttons
// for this to match.
document.addEventListener('click', function (e) {
    var button = e.target.closest ? e.target.closest('[data-nwm-embed-popup]') : null;
    if (button) {
        e.preventDefault();
        neowxEmbedPopup(button);
    }
});