document.getElementById('url-form').addEventListener('submit', async function(event) {
    event.preventDefault();  // Prevent default form submission behavior

    const urlInput = document.getElementById('url').value;

    // Remove any existing popups before creating new ones
    removePopup('success-popup');
    removePopup('error-popup');

        const response = await fetch('/add_url', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
                'url': urlInput
            })
        });

        const j = await response.json();
        if (!response.ok) {
            createPopup('error-popup', j.error || 'An unexpected error occurred.', 'red');
        } else {
            createPopup('success-popup', `Successfully added '${j.url}' to the queue!`, 'green');
        }

});

// Function to create and display popup dynamically
function createPopup(id, message, color) {
    const popup = document.createElement('div');
    popup.id = id;
    popup.textContent = message;
    popup.style.position = 'fixed';
    popup.style.top = '50%';
    popup.style.left = '50%';
    popup.style.transform = 'translate(-50%, -50%)'; // Center the popup
    popup.style.padding = '10px';
    popup.style.backgroundColor = color;
    popup.style.color = 'white';
    popup.style.borderRadius = '5px';
    popup.style.zIndex = '1000';
    document.body.appendChild(popup);

    // Auto-hide the popup after 3 seconds
    setTimeout(() => {
        removePopup(id);
    }, 3000);
}

// Function to remove popup by ID
function removePopup(id) {
    const popup = document.getElementById(id);
    if (popup) {
        document.body.removeChild(popup);
    }
}
