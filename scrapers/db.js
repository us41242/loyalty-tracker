const { createClient } = require('@supabase/supabase-js');

const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

function parseDate(dateStr) {
  if (!dateStr) return null;
  // MM/DD/YYYY
  const slashMatch = dateStr.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/);
  if (slashMatch) {
    return `${slashMatch[3]}-${slashMatch[1].padStart(2, '0')}-${slashMatch[2].padStart(2, '0')}`;
  }
  // "Mon DD, YYYY" or "Mon DD YYYY"
  const months = { Jan:'01', Feb:'02', Mar:'03', Apr:'04', May:'05', Jun:'06',
                    Jul:'07', Aug:'08', Sep:'09', Oct:'10', Nov:'11', Dec:'12' };
  const wordMatch = dateStr.match(/([A-Z][a-z]{2})\s+(\d{1,2}),?\s*(\d{4})/);
  if (wordMatch && months[wordMatch[1]]) {
    return `${wordMatch[3]}-${months[wordMatch[1]]}-${wordMatch[2].padStart(2, '0')}`;
  }
  // "Month DD, YYYY"
  const fullMonths = { January:'01', February:'02', March:'03', April:'04', May:'05', June:'06',
                       July:'07', August:'08', September:'09', October:'10', November:'11', December:'12' };
  const fullMatch = dateStr.match(/([A-Z][a-z]+)\s+(\d{1,2}),?\s*(\d{4})/);
  if (fullMatch && fullMonths[fullMatch[1]]) {
    return `${fullMatch[3]}-${fullMonths[fullMatch[1]]}-${fullMatch[2].padStart(2, '0')}`;
  }
  return dateStr;
}

module.exports = { supabase, parseDate };
