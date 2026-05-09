// v73: Global 12-hour time formatting
function formatTime12Hour(time) {
  if (!time) return '';
  
  // Handle various time formats
  let hours, minutes;
  
  if (time.includes(':')) {
    [hours, minutes] = time.split(':').map(t => parseInt(t));
  } else {
    hours = parseInt(time);
    minutes = 0;
  }
  
  const period = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12 || 12; // Convert 0 to 12 for midnight
  
  return `${hours}:${minutes.toString().padStart(2, '0')} ${period}`;
}

// Make available globally
window.formatTime12Hour = formatTime12Hour;
