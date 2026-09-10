TRANSFORMATION LASER STUDIO — LOCAL WEBSITE
==========================================

WHAT IS INCLUDED
- Responsive one-page customer website using your supplied logo.
- Service and price list in £.
- Compact/expandable Nails list.
- Booking request form with multiple-treatment selection.
- Automatic calculation of the selected treatment total.
- Requests are reviewed in the order received.
- Local Studio Admin page to review requests and confirm/decline them.
- Confirmed bookings are copied into a separate accepted-bookings data file.
- Studio Admin includes a weekly Excel (.xlsx) export for accepted appointments.
- New booking notifications are addressed to zaeemahmad365@gmail.com.
- Confirmation emails tell the customer their treatment(s), appointment date/time, and the studio enquiry contact details.

SALON OPENING HOURS
- Monday–Saturday: 10am–7pm
- Sunday: 11am–7pm, Nails only
The booking form and server both check these hours. Sunday requests are restricted to Nail treatments.

RUN IT IN GOOGLE CHROME
Windows:
1. Make sure Python 3 is installed.
2. Double-click run_windows.bat.
3. The website should open automatically. If it opens in another browser, copy:
   http://127.0.0.1:8000
   into Google Chrome.

macOS:
1. Make sure Python 3 is installed.
2. Double-click run_mac.command (you may need to allow it in Privacy & Security the first time).
3. Open http://127.0.0.1:8000 in Google Chrome if Chrome is not your default browser.

Linux:
1. Run ./run_linux.sh
2. Open http://127.0.0.1:8000 in Google Chrome/Chromium.

ADMIN / BOOKING REVIEW
- Admin page: http://127.0.0.1:8000/admin.html
- Current local PIN: Password123
- To change the PIN, open settings.env, change the ADMIN_PIN value, save it, then restart the website.
- Requests are displayed oldest first so you can review them in the order received.
- The booking-request cards work the same way as before.
- When you confirm a booking, a separate copy is saved to data/accepted_bookings.json.
- If a confirmed booking is later declined, it is removed from accepted_bookings.json so that file reflects currently accepted appointments.
- Use the "Weekly Excel export" box to choose an appointment week and download all accepted bookings scheduled in that week.
- The downloaded workbook is also saved automatically in data/exports using a name such as accepted-bookings-2026-W35.xlsx.

EMAIL DELIVERY
The website is configured so new booking notifications are addressed to:
  zaeemahmad365@gmail.com

For security, a Gmail password is not included in this download. Until Gmail SMTP is configured, outgoing messages are saved as .eml previews in:
  data/outbox

To make emails arrive in the Gmail inbox and send customer confirmations:
1. Open settings.env.
2. Keep SMTP_USER=zaeemahmad365@gmail.com.
3. Create a Google App Password for that Gmail account.
4. Paste the App Password after SMTP_PASSWORD= (do not use the normal Gmail password).
5. Save settings.env and restart the website.

The website sends:
- A new-request notification to zaeemahmad365@gmail.com as soon as a customer submits a booking.
- A confirmation or decline email to the customer's email address when you use the Studio Admin page.
- Confirmation emails state that the appointment has been made for the selected treatment(s) on the confirmed date/time.
- Customer emails include enquiries contact details:
  Email: zaeemahmad365@gmail.com
  Phone: 07719598265

EDITABLE SETTINGS
Open settings.env to change the admin PIN, studio email, phone number, port, or Gmail sending settings. Restart the website after changing the file.

SAME-TIME BOOKING RULES
The booking system is now treatment-aware. More than one customer can request and have an appointment confirmed for the same date and time when their treatments are different.

Examples:
- Nails at 2:00pm + Waxing at 2:00pm: allowed.
- Microneedling at 2:00pm + Botox at 2:00pm: allowed.
- The same exact treatment at 2:00pm twice: blocked while the first request is pending or confirmed.

If a booking contains several treatments, it clashes only if any exact treatment (category + treatment name + variant) is already pending or confirmed at that date/time. Price is not part of the availability check.

The same rule is checked again when Studio Admin confirms a booking, so older or manually edited data cannot accidentally double-confirm an exact treatment at the same time.

To change this logic later, open server.py and search for:
  def treatment_key
  requested_treatments
  if status == 'confirmed'

DATA
Booking requests are stored locally in:
  data/bookings.json

Accepted/confirmed bookings are also stored separately in:
  data/accepted_bookings.json

Weekly Excel exports are saved in:
  data/exports/

The Excel export uses the appointment date to decide which week a booking belongs to. The spreadsheet contains booking ID, appointment date/time, customer name, email, phone, treatments, categories, total, notes, accepted time and original request time.

Do not upload these files publicly without adding production-grade access controls and privacy/security measures because they contain customer information.

CUSTOM DOMAIN LATER
This is intentionally a local test build. Before publishing on a custom domain, use HTTPS, replace the default admin PIN with a strong private PIN, configure SMTP, and deploy the Python server behind a production web server or hosting platform.

EDITING THE TREATMENT LIST
The treatment catalogue is stored in:
  public/services.json

A new category called "Aesthetic Treatments" has been added there. The name is only a placeholder, so you can rename it by changing:
  "category": "Aesthetic Treatments"

Each aesthetic treatment has three editable fields:
  "name"        = the treatment name shown on the website and in the booking form
  "description" = the smaller description shown under the treatment name
  "price"       = the numeric price used by the website and booking system

The aesthetic prices are currently set to 0, which the website displays as £0.00 because all prices use the same pounds-sterling formatting. For example, change:
  "price": 0
To:
  "price": 75
for a £75.00 price.

The practitioner disclaimer is also in public/services.json under the Aesthetic Treatments category:
  "disclaimer": "All treatments carried out by a fully qualified aesthetic practitioner."

After editing services.json, save the file and refresh the website. If the server is already running, the visible service list will refresh normally, but restart the server before submitting bookings after changing names or prices so the server reloads the allowed treatment catalogue.

The display support for the small descriptions and category disclaimer is in:
  public/app.js
  public/styles.css
Normally you do not need to edit these files when changing treatment names, descriptions or prices.

ADMIN BOOKING EXPORT — FILES TO EDIT
-----------------------------------
The weekly accepted-booking export is split across these files:

1. server.py
   - Creates and maintains data/accepted_bookings.json.
   - Copies a booking into accepted_bookings.json when you press Confirm booking.
   - Removes it from that separate file if the booking is later declined.
   - Builds the .xlsx file without needing an extra Python package.
   - Filters exports by the appointment week chosen in Studio Admin.
   - Saves a copy into data/exports and sends the same file to Chrome for download.

2. public/admin.html
   - Contains the visible "Weekly Excel export" section, week picker and export button.
   - Change the wording or button label here if you want different text.

3. public/admin.js
   - Sets the week picker to the current week.
   - Sends the chosen week and admin PIN to the server.
   - Starts the .xlsx download in the browser and shows the export result message.

4. public/styles.css
   - Contains the .accepted-export styling so the new controls match the existing website design.

5. data/accepted_bookings.json
   - This is generated/maintained data, not website code.
   - It stores only bookings that are currently confirmed.
   - It is separate from data/bookings.json, which continues to hold the original booking requests and statuses.

6. data/exports/
   - Each time you export a week, the current version of that week's Excel workbook is saved here.
   - Exporting the same week again refreshes/overwrites that week's workbook with the latest accepted bookings.


BOOKING EXPORT FIX / TROUBLESHOOTING
- This build repairs accepted-booking data automatically when the server starts and again before every weekly export.
- If bookings.json already contains a booking with status "confirmed", it will be copied into data/accepted_bookings.json automatically.
- Studio Admin now shows the exact accepted_bookings.json path used by the running server and the number of accepted bookings currently saved. This is useful if you have several extracted copies of the website.
- If port 8000 is already being used by an older copy of the website, this build automatically tries 8001, 8002 and so on, then opens the correct new address in Chrome. Read the black command window to see the exact Website/Admin address.
- Chrome caching is disabled for this local build so old admin JavaScript should not be reused after an update.
- settings.env now overrides a legacy .env file. This means changing ADMIN_PIN in settings.env reliably changes the PIN after restart.
- Weekly export still downloads even if Windows/Excel has locked the usual saved-copy filename. In that case a timestamped server copy is used where possible.

If a weekly export shows 0 rows, check the APPOINTMENT date of the confirmed booking. The week selector is based on the appointment date, not the date the booking was confirmed.
