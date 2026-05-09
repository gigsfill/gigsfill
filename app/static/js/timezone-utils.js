/**
 * GigsFill Timezone Utilities
 * 
 * SQLite CURRENT_TIMESTAMP stores UTC without 'Z' suffix.
 * This utility ensures consistent timezone-aware display across the site.
 * 
 * Usage:
 *   formatUTC(timestampStr)              → display in user's detected timezone
 *   formatUTC(timestampStr, 'short')     → shorter format (no seconds)
 *   formatUTC(timestampStr, 'date')      → date only
 *   formatUTC(timestampStr, 'time')      → time only
 *   formatUTC(timestampStr, 'relative')  → "2 hours ago", "yesterday", etc.
 */

// US State → IANA Timezone mapping
const STATE_TIMEZONES = {
  // Eastern Time
  'CT': 'America/New_York', 'DE': 'America/New_York', 'DC': 'America/New_York',
  'FL': 'America/New_York', 'GA': 'America/New_York', 'ME': 'America/New_York',
  'MD': 'America/New_York', 'MA': 'America/New_York', 'MI': 'America/New_York',
  'NH': 'America/New_York', 'NJ': 'America/New_York', 'NY': 'America/New_York',
  'NC': 'America/New_York', 'OH': 'America/New_York', 'PA': 'America/New_York',
  'RI': 'America/New_York', 'SC': 'America/New_York', 'VT': 'America/New_York',
  'VA': 'America/New_York', 'WV': 'America/New_York',
  // Central Time
  'AL': 'America/Chicago', 'AR': 'America/Chicago', 'IL': 'America/Chicago',
  'IA': 'America/Chicago', 'KS': 'America/Chicago', 'KY': 'America/Chicago',
  'LA': 'America/Chicago', 'MN': 'America/Chicago', 'MS': 'America/Chicago',
  'MO': 'America/Chicago', 'NE': 'America/Chicago', 'ND': 'America/Chicago',
  'OK': 'America/Chicago', 'SD': 'America/Chicago', 'TN': 'America/Chicago',
  'TX': 'America/Chicago', 'WI': 'America/Chicago',
  // Mountain Time
  'AZ': 'America/Phoenix', 'CO': 'America/Denver', 'ID': 'America/Boise',
  'MT': 'America/Denver', 'NM': 'America/Denver', 'UT': 'America/Denver',
  'WY': 'America/Denver',
  // Pacific Time
  'CA': 'America/Los_Angeles', 'NV': 'America/Los_Angeles',
  'OR': 'America/Los_Angeles', 'WA': 'America/Los_Angeles',
  // Alaska & Hawaii
  'AK': 'America/Anchorage', 'HI': 'Pacific/Honolulu',
  // Territories
  'PR': 'America/Puerto_Rico', 'GU': 'Pacific/Guam', 'VI': 'America/Virgin',
  'AS': 'Pacific/Pago_Pago', 'MP': 'Pacific/Guam',
  // Full state names (for flexibility)
  'Connecticut': 'America/New_York', 'Delaware': 'America/New_York',
  'District of Columbia': 'America/New_York', 'Florida': 'America/New_York',
  'Georgia': 'America/New_York', 'Maine': 'America/New_York',
  'Maryland': 'America/New_York', 'Massachusetts': 'America/New_York',
  'Michigan': 'America/New_York', 'New Hampshire': 'America/New_York',
  'New Jersey': 'America/New_York', 'New York': 'America/New_York',
  'North Carolina': 'America/New_York', 'Ohio': 'America/New_York',
  'Pennsylvania': 'America/New_York', 'Rhode Island': 'America/New_York',
  'South Carolina': 'America/New_York', 'Vermont': 'America/New_York',
  'Virginia': 'America/New_York', 'West Virginia': 'America/New_York',
  'Alabama': 'America/Chicago', 'Arkansas': 'America/Chicago',
  'Illinois': 'America/Chicago', 'Indiana': 'America/Indiana/Indianapolis',
  'Iowa': 'America/Chicago', 'Kansas': 'America/Chicago',
  'Kentucky': 'America/Chicago', 'Louisiana': 'America/Chicago',
  'Minnesota': 'America/Chicago', 'Mississippi': 'America/Chicago',
  'Missouri': 'America/Chicago', 'Nebraska': 'America/Chicago',
  'North Dakota': 'America/Chicago', 'Oklahoma': 'America/Chicago',
  'South Dakota': 'America/Chicago', 'Tennessee': 'America/Chicago',
  'Texas': 'America/Chicago', 'Wisconsin': 'America/Chicago',
  'Arizona': 'America/Phoenix', 'Colorado': 'America/Denver',
  'Idaho': 'America/Boise', 'Montana': 'America/Denver',
  'New Mexico': 'America/Denver', 'Utah': 'America/Denver',
  'Wyoming': 'America/Denver',
  'California': 'America/Los_Angeles', 'Nevada': 'America/Los_Angeles',
  'Oregon': 'America/Los_Angeles', 'Washington': 'America/Los_Angeles',
  'Alaska': 'America/Anchorage', 'Hawaii': 'Pacific/Honolulu',
  'IN': 'America/Indiana/Indianapolis'
};

// Timezone abbreviation labels
const TZ_LABELS = {
  'America/New_York': 'ET', 'America/Chicago': 'CT',
  'America/Denver': 'MT', 'America/Los_Angeles': 'PT',
  'America/Phoenix': 'AZ', 'America/Boise': 'MT',
  'America/Anchorage': 'AKT', 'Pacific/Honolulu': 'HT',
  'America/Indiana/Indianapolis': 'ET'
};

// Global timezone - set by page on load, defaults to browser timezone
window._gfTimezone = null;
window._gfTzLabel = null;

/**
 * Set the timezone for the current page based on a US state
 */
function setTimezoneFromState(state) {
  if (!state) return;
  const tz = STATE_TIMEZONES[state] || STATE_TIMEZONES[state.trim()];
  if (tz) {
    window._gfTimezone = tz;
    window._gfTzLabel = TZ_LABELS[tz] || '';
  }
}

/**
 * Set timezone explicitly (e.g., for admin page)
 */
function setTimezone(ianaTimezone) {
  window._gfTimezone = ianaTimezone;
  window._gfTzLabel = TZ_LABELS[ianaTimezone] || '';
}

/**
 * Get the active timezone (falls back to browser default)
 */
function getTimezone() {
  return window._gfTimezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/**
 * Parse a UTC timestamp string from SQLite into a proper Date object.
 * SQLite CURRENT_TIMESTAMP gives "2026-02-11 16:48:37" (UTC, no Z).
 * We append 'Z' to ensure JavaScript treats it as UTC.
 */
function parseUTC(timestampStr) {
  if (!timestampStr) return null;
  let str = String(timestampStr).trim();
  // If it's a date-only string (YYYY-MM-DD), don't treat as UTC timestamp
  if (/^\d{4}-\d{2}-\d{2}$/.test(str)) {
    // Parse as local date to avoid off-by-one timezone issues
    const [y, m, d] = str.split('-').map(Number);
    return new Date(y, m - 1, d);
  }
  // Replace space with T for ISO format, add Z if not present
  if (str.includes(' ') && !str.includes('T')) {
    str = str.replace(' ', 'T');
  }
  if (!str.endsWith('Z') && !str.includes('+') && !str.includes('-', 10)) {
    str += 'Z';
  }
  return new Date(str);
}

/**
 * Format a UTC timestamp for display.
 * 
 * @param {string} timestampStr - UTC timestamp from SQLite
 * @param {string} format - 'full' (default), 'short', 'date', 'time', 'relative'
 * @param {boolean} showTz - whether to append timezone label (default: true)
 * @returns {string} Formatted date/time string
 */
function formatUTC(timestampStr, format, showTz) {
  if (!timestampStr) return '';
  
  const date = parseUTC(timestampStr);
  if (!date || isNaN(date.getTime())) return timestampStr;
  
  const tz = getTimezone();
  const tzLabel = (showTz !== false && window._gfTzLabel) ? ` ${window._gfTzLabel}` : '';
  
  if (format === 'relative') {
    return _relativeTime(date);
  }
  
  if (format === 'date') {
    return date.toLocaleDateString('en-US', {
      timeZone: tz, month: 'short', day: 'numeric', year: 'numeric'
    });
  }
  
  if (format === 'time') {
    return date.toLocaleTimeString('en-US', {
      timeZone: tz, hour: 'numeric', minute: '2-digit', hour12: true
    }) + tzLabel;
  }
  
  if (format === 'short') {
    return date.toLocaleDateString('en-US', {
      timeZone: tz, month: 'short', day: 'numeric', year: 'numeric'
    }) + ' ' + date.toLocaleTimeString('en-US', {
      timeZone: tz, hour: 'numeric', minute: '2-digit', hour12: true
    }) + tzLabel;
  }
  
  // Full format (default)
  return date.toLocaleString('en-US', {
    timeZone: tz, month: 'short', day: 'numeric', year: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true
  }) + tzLabel;
}

/**
 * Format a date-only string (YYYY-MM-DD) for display.
 * No timezone conversion - dates are already local.
 */
function formatDateLocal(dateStr) {
  if (!dateStr) return '';
  const [y, m, d] = dateStr.split('-').map(Number);
  if (!y || !m || !d) return dateStr;
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric'
  });
}

/**
 * Relative time display ("2 hours ago", "yesterday", etc.)
 */
function _relativeTime(date) {
  const now = new Date();
  const diffMs = now - date;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);
  
  if (diffSec < 60) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  if (diffDay === 1) return 'yesterday';
  if (diffDay < 7) return `${diffDay}d ago`;
  if (diffDay < 30) return `${Math.floor(diffDay / 7)}w ago`;
  
  return formatUTC(date.toISOString(), 'short', false);
}

/**
 * Get timezone info string for display (e.g., "Pacific Time (PT)")
 */
function getTimezoneDisplay() {
  const tz = getTimezone();
  const label = TZ_LABELS[tz];
  if (label) {
    const names = {
      'ET': 'Eastern Time', 'CT': 'Central Time', 'MT': 'Mountain Time',
      'PT': 'Pacific Time', 'AZ': 'Arizona Time', 'AKT': 'Alaska Time',
      'HT': 'Hawaii Time'
    };
    return `${names[label] || label} (${label})`;
  }
  return tz;
}
