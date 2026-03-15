// =============================================================
//  INSTAASTRO — APPS SCRIPT TRIGGER (v2)
//  Now just calls the Render Python app
//  Completes in < 5 seconds — no 6 min limit issue
// =============================================================

var TRIGGER_CONFIG = {
  RENDER_URL: "https://your-app-name.onrender.com", // replace after deploying
  WHATSAPP_NUMBER: "91XXXXXXXXXX",                  // default send-to number
};

// ─────────────────────────────────────────────────────────────
//  Trigger report for a single astro
// ─────────────────────────────────────────────────────────────

function triggerReportForAstro(astroId, astroName, whatsappNumber) {
  var url      = TRIGGER_CONFIG.RENDER_URL + "/generate-report";
  var phone    = whatsappNumber || TRIGGER_CONFIG.WHATSAPP_NUMBER;

  Logger.log("Triggering report for astro_id: " + astroId);

  var resp = UrlFetchApp.fetch(url, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    payload: JSON.stringify({
      astro_id:         astroId,
      astro_name:       astroName || ("Astro " + astroId),
      whatsapp_number:  phone,
      days:             3
    }),
    muteHttpExceptions: true
  });

  var code   = resp.getResponseCode();
  var result = resp.getContentText();

  Logger.log("Response (" + code + "): " + result);

  if (code === 200) {
    Logger.log("Report triggered successfully for astro_id: " + astroId);
    return true;
  } else {
    Logger.log("Failed for astro_id: " + astroId + " — " + result);
    return false;
  }
}

// ─────────────────────────────────────────────────────────────
//  Trigger reports for multiple astros
//  Each call takes ~5 seconds — well within 6 min limit
// ─────────────────────────────────────────────────────────────

function triggerReportsForAllAstros() {
  // Add your astro IDs and names here
  var astros = [
    { id: 4562, name: "Astro Sumesh",  whatsapp: "91XXXXXXXXXX" },
    { id: 2594, name: "Astro Priya",   whatsapp: "91XXXXXXXXXX" },
    { id: 2179, name: "Astro Raj",     whatsapp: "91XXXXXXXXXX" },
    { id: 1726, name: "Astro Meena",   whatsapp: "91XXXXXXXXXX" },
  ];

  var success = 0;
  astros.forEach(function(astro) {
    var ok = triggerReportForAstro(astro.id, astro.name, astro.whatsapp);
    if (ok) success++;
    Utilities.sleep(1000); // 1 sec between triggers
  });

  Logger.log("Triggered " + success + "/" + astros.length + " reports.");
}

// ─────────────────────────────────────────────────────────────
//  Setup daily trigger at 9am
// ─────────────────────────────────────────────────────────────

function setupDailyTrigger() {
  ScriptApp.getProjectTriggers().forEach(function(t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger("triggerReportsForAllAstros")
    .timeBased()
    .everyDays(1)
    .atHour(9)
    .create();
  Logger.log("Daily trigger set for 9am.");
}

// ─────────────────────────────────────────────────────────────
//  TEST: trigger one astro manually
// ─────────────────────────────────────────────────────────────

function test_TriggerOneAstro() {
  triggerReportForAstro(4562, "Astro Sumesh", "91XXXXXXXXXX");
}

function test_HealthCheck() {
  var resp = UrlFetchApp.fetch(TRIGGER_CONFIG.RENDER_URL + "/", { muteHttpExceptions: true });
  Logger.log("Health: " + resp.getContentText());
}
