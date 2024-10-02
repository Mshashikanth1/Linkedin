function clickButtons() {
  var buttons = document.querySelectorAll("button");

  buttons.forEach(function(button) {
    // Check for "Following," "Show more results," and "Unfollow"
    if (button.innerText.trim() === "Following" || button.innerText.trim() === "Show more results" || button.innerText.trim() === "Unfollow") {
      button.click();
    }
  });
}

// Click buttons initially
clickButtons();

// Load more results and click buttons again after a delay
setTimeout(function() {
  clickButtons();
}, 2000); // Adjust the delay as needed

// Optional: Repeat the process multiple times to ensure all buttons are clicked
setTimeout(function() {
  clickButtons();
}, 4000); // Adjust for additional loads if necessary


/*just paste this  in browser console by oppeinging followers screen*/
