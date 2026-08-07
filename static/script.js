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

    busy(document.getElementById('verify-form'),
         document.getElementById('verify-btn'), 'Verifying...');

    var scanForm = document.querySelector('form[action="/"]');
    busy(scanForm, scanForm && scanForm.querySelector('button[type="submit"]'),
         'Scanning...');
});
