// Both actions are slow enough that a dead-looking button invites a second
// click: scanning waits on Gemini, verifying launches a browser. Disable and
// label on submit so it is obvious something is happening.
document.addEventListener('DOMContentLoaded', function () {
    function busy(form, button, label) {
        if (!form || !button) return;
        form.addEventListener('submit', function () {
            button.disabled = true;
            button.innerHTML =
                '<span class="spinner-border spinner-border-sm me-2" ' +
                'role="status" aria-hidden="true"></span>' + label;
        });
    }

    // Both say roughly how long: a scan is one Gemini round-trip per image
    // and per input, and verifying launches a browser. A bare spinner on a
    // request that takes minutes reads as a hang, and the user reloads --
    // which doubles the work rather than cancelling it.
    busy(document.getElementById('verify-form'),
         document.getElementById('verify-btn'),
         'Verifying — this takes a moment...');

    var scanForm = document.querySelector('form[action="/"]');
    busy(scanForm, scanForm && scanForm.querySelector('button[type="submit"]'),
         'Scanning — this can take a minute...');
});
