document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.popup').forEach(function(popup) {
        setTimeout(function() {
            popup.classList.remove('show');
            setTimeout(function() {
                if (popup.parentElement) {
                    popup.parentElement.removeChild(popup);
                }
            }, 300);
        }, 3000);
    });
});