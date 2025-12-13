console.log("Hello from index.js");

// Keep the session alive by scrolling the window every minute. Useful for kiosk mode.
setInterval(() => {
  window.scrollBy(0,1);
  window.scrollBy(0,-1);
}, 60000);
