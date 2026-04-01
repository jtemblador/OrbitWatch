
(function() {

    /**
     * Detects keyup events and executes callback after specified period of time
     * @param {Object} o configuration options
     *   wait : in milliseconds how long to wait after the last key was pressed to execute callback.  Dfeault 500
     *   callback : the function to execute.  Passes the text value as param
     *   captureLength : minimum amount of characters before callback is executed.  Default -1
     *   fireOnEmpty : true if callback is executed when there are no value
     */
    jQuery.fn.typeWatch = function(o) {
        // Options
        var options = jQuery.extend({
            wait:500,
            callback: function() {},
            captureLength : -1,
            fireOnEmpty : true
        },o);

        function checkElement(timer, override) {
            var elTxt = jQuery(timer.el).val();

            if ((elTxt.length >= options.captureLength && elTxt.toUpperCase() != timer.text.toUpperCase())
                    || (override && elTxt.length >= options.captureLength)
                    || (options.fireOnEmpty && elTxt.length == 0 && timer.text)) {
                timer.text = elTxt;
                timer.cb(elTxt);
            }
        }

        function watchElement(elem) {
            // Must be input
            if (elem.type.toUpperCase() === "SEARCH" ) {

                // Allocate timer element
                var timer = {
                    timer : null,
                    text : jQuery(elem).val(),
                    cb : options.callback,
                    el : elem,
                    wait : options.wait
                };

                // Key watcher / clear and reset the timer
                var startWatch = function(evt) {
                    var timerWait = timer.wait;
                    var overrideBool = false;

                    // If enter is pressed then directly execute the callback
                    if (evt.keyCode == 13 && this.type.toUpperCase() === "INPUT") {
                        timerWait = 1;
                        overrideBool = true;
                    }

                    var timerCallbackFx = function()
                    {
                        checkElement(timer, overrideBool)
                    };

                    // Clear timer
                    clearTimeout(timer.timer);
                    timer.timer = setTimeout(timerCallbackFx, timerWait);
                };

                jQuery(elem).keyup(startWatch);
            }
        }

        // Watch Each Element
        return this.each(function(){
            watchElement(this);
        });
    };


    /**
     * This method takes a date object in local browser datetime
     * and converts it to UTC.
     */
    $.extend({
        convertDateToUTC: function(date) {
            return new Date(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate(), date.getUTCHours(), date.getUTCMinutes(), date.getUTCSeconds());
        }
    });

    $.extend({
        // Pass in the message and the jQuery selector of the div to display it in
        setResponseMessage: function (msg, sel) {
            $(sel).empty().html(msg);
        }
    });


    /**
     * jQuery extension method to filter a select list by a text input value
     */
    jQuery.fn.filterByText = function(textbox, selected) {
        return this.each(function() {
            var select = this;
            var options = [];
            $(select).find('option').each(function() {
                options.push({
                    value: $(this).val(),
                    text: $(this).text()
                });
            });
            $(select).data('options', options);

            $(textbox).bind('change keyup', function() {
                var options = $(select).empty().data('options');
                var search = $.trim($(this).val());
                var regex = new RegExp(search, "gi");

                $.each(options, function(i) {
                    var option = options[i];
                    var sel = '';
                    if (option.text.match(regex) !== null) {
                        if (selected && option.text === selected) {
                            sel = ' selected="selected"';
                        }
                        $(select).append(
                            $('<option' + sel + '>').text(option.text).val(option.value)
                        );
                    }
                });
                // Make sure the selected option is in view
                select.scrollTop = selected.offsetTop - select.offsetTop;
            });
        });
    };

    /**
     * jQuery extension method to filter a select list by a text input value.
     * This method can be called directly as opposed to binding to events.
     */
    jQuery.fn.filterByTextOnPageLoad = function(textbox, selected) {
        return this.each(function() {
            var select = this;
            var options = $(select).empty().data('options');
            var search = $.trim($(textbox).val());
            var regex = new RegExp(search, "gi");

            $.each(options, function(i) {
                var option = options[i];
                var sel = '';
                if (option.text.match(regex) !== null) {
                    if (selected && option.text === selected) {
                        sel = ' selected="selected"';
                    }
                    $(select).append(
                        $('<option' + sel + '>').text(option.text).val(option.value)
                    );
                }
            });
            // Make sure the selected option is in view
            select.scrollTop = selected.offsetTop - select.offsetTop;
        });
    };
})(jQuery);

// Ensure that when a tab is clicked, it updates the URL with the hash
$(function () {
    var hash = window.location.hash;
    hash && $('ul.nav a[href="' + hash + '"]').tab('show');

    $('.nav-tabs a').click(function (e) {
        $(this).tab('show');
        var scrollmem = $('body').scrollTop();
        window.location.hash = this.hash;
        $('html,body').scrollTop(scrollmem);
    });
});

$(function () {
    function activateTab() {
        var activeTab = $('[href="' + window.location.hash.replace('/', '') + '"]');
        if ($.isFunction(activeTab.tab))
            activeTab && activeTab.tab('show');
    }

    activateTab();

    $(window).on('hashchange', function () {
        activateTab();
    });

    $('a[data-toggle="tab"], a[data-toggle="pill"]').on('shown', function () {
        window.location.hash = '/' + $(this).attr('href').replace('#', '');
    });
});


$(function () {
    // Only check the session if we are on a page where login is required
    if ( !(window.location.href.indexOf("/auth/") > -1) &&
         !(window.location.href.indexOf("/documentation") > -1) ) {

        // Get the CI config value for session expiration and set the JS var
        $.get('/auth/getSessionExpiration', function(sessionTimeout){
            startCheckSessionTimer(sessionTimeout);
        });

        // Take the session timeout value, set an interval to check,
        // and start the timer to check the session.
        function startCheckSessionTimer(sessionTimeout) {
            // Make sure we have a valid number for sessionTimeout
            if (isNaN(sessionTimeout)) {
                sessionTimeout = 7200; // Default: check every 2 hours
            }
            var parsedSessionTimeout = parseInt(sessionTimeout);
            if (isNaN(parsedSessionTimeout)) {
                parsedSessionTimeout = 7200; // Default: check every 2 hours
            }
            // Add 15 seconds to the session timeout so the first check happens
            // after expiration and set the interval in milliseconds
            var checkInterval = (parsedSessionTimeout + 15) * 1000;
            // Set a timer to check the session every x milliseconds
            setInterval(CheckForSession, checkInterval);
        }

        function CheckForSession() {
            // Do the session check on the server side
            $.ajax({
                url: '/auth/checkSession',
                type: 'post',
                cache: false,
                success: function (res) {
                    var isExpired = (res === '1');
                    if (isExpired) {
                        // If the session has expired, make sure cleanup is done
                        // on the server and then redirect on the client-side
                        // $.post("/auth/expireSession", function () {
                            window.location.href = '/auth/login';
                        // });
                    }
                }
            });
        }
    }
});

$(function () {
    Array.prototype.removeDuplicates = function () {
        return this.filter(function (item, index, self) {
            return self.indexOf(item) === index;
        });
    };
});

function clearNotificationMessages( divId ) {
    $( divId ).empty();
}

/**
 * Dynamically generate the alerts for the error container.
 *
 * @param errors String[] Array of error messages as strings.
 */
function generateErrorMessages( divId, errors ) {
    $( divId ).append(
        "<div class=\"alert alert-danger\"><a href=\"#\" class=\"close\" data-dismiss=\"alert\" aria-label=\"close\">&times;</a>" + "<p>" + errors + "</p></div>"
    );
}

function generateSuccessMessages( divId, successes ) {
    $( divId ).append(
        "<div class=\"alert alert-success\"><a href=\"#\" class=\"close\" data-dismiss=\"alert\" aria-label=\"close\">&times;</a>" + "<p>" + successes + "</p></div>"
    );
}

window.data = { };