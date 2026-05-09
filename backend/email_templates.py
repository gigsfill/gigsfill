"""
Email Templates for GigsFill
=============================
Auto-generated from database via Admin > Export All.
Do not edit manually - changes will be overwritten on next export.
"""
import logging
import sqlite3
from datetime import datetime
logger = logging.getLogger("gigsfill.admin")

TEMPLATES = {

    "artist_gig_booked": {
        "subject": "You're booked at {{venue_name}}!",
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #059669;">You&#39;re Booked!</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, your booking at <strong>{{venue_name}}</strong> is confirmed. Here are the full details:</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Address</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_address_link}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}} &#8211; {{end_time}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Pay</td>
<td style="padding: 6px 0; font-size: 14px; color: #059669; font-weight: 600;">${{pay}}</td>
</tr>
{{#artist_type}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Type</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{artist_type}}</td>
</tr>{{/artist_type}}
{{#band_formats}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lineup</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{band_formats}}</td>
</tr>{{/band_formats}}
{{#styles}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Styles</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{styles}}</td>
</tr>{{/styles}}
<tr>
<td colspan="2" style="padding: 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; vertical-align: top;">Notes</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500; white-space: pre-wrap;">{{notes_to_artist}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 4px 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Capacity</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_capacity}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Arrival</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{arrival_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Bar Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{bar_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Food Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{food_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Stage</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{stage_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Sound</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{sound_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Engineer</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{engineer_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lighting</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{lighting_info}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}" style="display: inline-block; background: #059669; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View My Gigs</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "artist_gig_cancelled": {
        "subject": 'Gig cancelled at {{venue_name}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Gig Cancelled</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, unfortunately your gig has been cancelled.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fef2f2; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 80px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{slot_times}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Reason</td>
<td style="padding: 6px 0; font-size: 14px; color: #dc2626;">{{cancellation_reason}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">Log into your account and see what else is available.</p>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Browse Gigs</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "artist_preferred_request": {
        "subject": 'Preferred request sent to {{venue_name}}',
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Request Sent</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hey {{artist_name}}, your preferred artist request has been sent to <strong>{{venue_name}}</strong>.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">If approved, you&#39;ll be able to auto-book gigs at this venue. We&#39;ll notify you when they respond.</p>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&open_gig={{gig_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View Status</a>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>'''
    },

    "artist_preferred_approved": {
        "subject": "You're now preferred at {{venue_name}}",
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">You&#39;re Approved!</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Great news, {{artist_name}} - <strong>{{venue_name}}</strong> has approved you as a preferred artist.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">You can now book available gigs at this venue without waiting for approval.&#160; Some Venues have frequency limitations...</p>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Book a Gig</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "artist_preferred_denied": {
        "subject": 'Preferred request update from {{venue_name}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Request Denied</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, <strong>{{venue_name}}</strong> wasn&#39;t able to approve your preferred artist request at this time.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">Keep building your profile to showcase your talents and try again later.</p>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Browse Gigs</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "artist_preferred_revoked": {
        "subject": 'Preferred status ended at {{venue_name}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Preferred Status Ended</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, <strong>{{venue_name}}</strong> has ended your preferred artist status.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">Any existing confirmed gigs will remain scheduled.&#160;&#160;</p>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View My Gigs</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_gig_booked": {
        "subject": '{{artist_name}} booked a gig',
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Gig Booked</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;"><strong>{{artist_name}}</strong> has booked a gig at <strong>{{venue_name}}</strong>.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 6px; margin-bottom: 24px;">
<tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 80px;">Artist</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{artist_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}} &#8211; {{end_time}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Pay</td>
<td style="padding: 6px 0; font-size: 14px; color: #059669; font-weight: 600;">${{pay}}</td>
</tr>
</table>
</td></tr>
</table>
<a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View Calendar</a>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>'''
    },

    "venue_booking_approval_request": {
        "subject": "{{artist_name}} is requesting same-day booking at {{venue_name}}",
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 8px 0; font-size: 22px; font-weight: 600; color: #d97706;">Same-Day Booking Request</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;"><strong>{{artist_name}}</strong> wants to book a gig at <strong>{{venue_name}}</strong> <strong>today</strong>. Please approve or deny this request.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fffbeb; border: 1px solid #fcd34d; border-radius: 6px; margin-bottom: 24px;">
<tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 80px;">Artist</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{artist_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}} &#8211; {{end_time}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Pay</td>
<td style="padding: 6px 0; font-size: 14px; color: #059669; font-weight: 600;">${{pay}}</td>
</tr>
{{#slot_info}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Slot</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{slot_info}}</td>
</tr>{{/slot_info}}
</table>
</td></tr>
</table>
<table role="presentation" cellspacing="0" cellpadding="0" border="0">
<tr>
<td style="padding-right: 12px;">
<a href="{{approve_url}}" style="display:inline-block;background:#059669;color:#ffffff;padding:13px 28px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:700;">&#10003; Approve</a>
</td>
<td>
<a href="{{deny_url}}" style="display:inline-block;background:#dc2626;color:#ffffff;padding:13px 28px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:700;">&#10005; Deny</a>
</td>
</tr>
</table>
<p style="margin: 16px 0 0 0; font-size: 12px; color: #9ca3af; text-align: center;">Or manage this from your <a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="color:#6b7280;">GigsFill calendar</a>.</p>
</td></tr>
<tr><td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>'''
    },

    "artist_booking_pending_approval": {
        "subject": "Booking request sent to {{venue_name}}",
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 8px 0; font-size: 22px; font-weight: 600; color: #d97706;">Booking Request Sent</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, your booking request for <strong>{{venue_name}}</strong> today has been sent. The venue will approve or deny shortly - we&#39;ll email you either way.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fffbeb; border: 1px solid #fcd34d; border-radius: 6px; margin-bottom: 24px;">
<tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 80px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}} &#8211; {{end_time}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Pay</td>
<td style="padding: 6px 0; font-size: 14px; color: #059669; font-weight: 600;">${{pay}}</td>
</tr>
{{#slot_info}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Slot</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{slot_info}}</td>
</tr>{{/slot_info}}
</table>
</td></tr>
</table>
</td></tr>
<tr><td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>'''
    },

    "artist_booking_approved": {
        "subject": "Booking approved - you're on at {{venue_name}} today!",
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 8px 0; font-size: 22px; font-weight: 600; color: #059669;">You&#39;re Approved!</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, <strong>{{venue_name}}</strong> has approved your booking request. You&#39;re confirmed for today!</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f0fdf4; border: 1px solid #86efac; border-radius: 6px; margin-bottom: 24px;">
<tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 80px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}} &#8211; {{end_time}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Pay</td>
<td style="padding: 6px 0; font-size: 14px; color: #059669; font-weight: 600;">${{pay}}</td>
</tr>
{{#slot_info}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Slot</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{slot_info}}</td>
</tr>{{/slot_info}}
</table>
</td></tr>
</table>
</td></tr>
<tr><td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>'''
    },

    "artist_booking_denied": {
        "subject": "Booking request denied by {{venue_name}}",
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 8px 0; font-size: 22px; font-weight: 600; color: #dc2626;">Booking Request Denied</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, unfortunately <strong>{{venue_name}}</strong> has denied your same-day booking request.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fef2f2; border: 1px solid #fca5a5; border-radius: 6px; margin-bottom: 24px;">
<tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 80px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}} &#8211; {{end_time}}</td>
</tr>
</table>
</td></tr>
</table>
<p style="margin: 0 0 0 0; font-size: 14px; color: #6b7280;">Check your calendar for other available gigs.</p>
</td></tr>
<tr><td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>'''
    },

    "venue_gig_cancelled": {
        "subject": '{{artist_name}} cancelled their gig on {{date}}',
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Gig Cancelled</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">A gig at <strong>{{venue_name}}</strong> has been cancelled.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fef2f2; border-radius: 6px; margin-bottom: 24px;">
<tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 80px;">Artist</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{artist_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{slot_times}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Reason</td>
<td style="padding: 6px 0; font-size: 14px; color: #dc2626;">{{cancellation_reason}}</td>
</tr>
</table>
</td></tr>
</table>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">{{waitlist_message}}</p>
<a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View Calendar</a>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>'''
    },

    "waitlist_exhausted_venue": {
        "subject": "Action needed: your gig on {{date}} is still open",
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 8px 0; font-size: 22px; font-weight: 600; color: #111827;">&#9888;&#65039; Gig Still Open</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi <strong>{{venue_name}}</strong> - a gig was cancelled and all waitlisted artists have either declined or not responded. <strong>This gig is still open with no performer booked.</strong></p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fff7ed; border: 1px solid #fed7aa; border-radius: 6px; margin-bottom: 24px;">
<tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 100px;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}}{{end_time_str}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Hours Away</td>
<td style="padding: 6px 0; font-size: 14px; color: #dc2626; font-weight: 600;">{{hours_until}} hours</td>
</tr>
</table>
</td></tr>
</table>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; margin-bottom: 24px;">
<tr><td style="padding: 16px 20px;">
<p style="margin: 0 0 8px 0; font-size: 14px; font-weight: 600; color: #15803d;">&#9989; Here&#39;s what we&#39;ve already done:</p>
<p style="margin: 0 0 4px 0; font-size: 14px; color: #166534;">&#9989; All of your preferred artists have been notified</p>
<p style="margin: 0; font-size: 14px; color: #166534;">{{radius_line}}</p>
</td></tr>
</table>
<p style="margin: 0 0 24px 0; font-size: 14px; line-height: 1.6; color: #4b5563;">{{blast_summary}} If you&#39;d like to reach out to artists directly or make any changes, head to your gig page.</p>
<a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="display: inline-block; background: #1d4ed8; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View Gig &#8594;</a>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>'''
    },

    "venue_preferred_request": {
        "subject": '{{artist_name}} wants to be a preferred artist',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Preferred Artist Request</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;"><strong>{{artist_name}}</strong> has requested to become a preferred artist at <strong>{{venue_name}}</strong>.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">Preferred artists can auto-book your available gigs without requiring approval each time. Review their profile and decide, you can always revoke their preferred status later.</p>
<a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Review Request</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_preferred_approved": {
        "subject": "You approved {{artist_name}} as preferred",
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Artist Approved</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">You&#39;ve approved <strong>{{artist_name}}</strong> as a preferred artist at <strong>{{venue_name}}</strong>.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">They can now auto-book any of your available gigs but are limited to your frequency limitations set in your profile. You can always revoke this status anytime from the "My Artists" tab..</p>
<a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Manage Venue</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_preferred_denied": {
        "subject": 'Preferred request declined',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Request Declined</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">You&#39;ve declined <strong>{{artist_name}}</strong>'s request to become a preferred artist at <strong>{{venue_name}}</strong>.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">You can always approve them later if you change your mind.</p>
<a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View Requests</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_preferred_revoked": {
        "subject": 'Preferred status ended for {{artist_name}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Status Revoked</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">You&#39;ve revoked preferred artist status for <strong>{{artist_name}}</strong> at <strong>{{venue_name}}</strong>.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">They can no longer auto-book gigs. Any existing confirmed bookings remain scheduled. You can cancel their remaining booked gigs individually from your Calendar if needed.</p>
<a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Manage Venue</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "entity_invitation": {
        "subject": "You've been invited to manage {{entity_name}} on GigsFill",
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">You&#39;re Invited</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;"><strong>{{inviter_name}}</strong> has invited you to help manage the {{entity_type}} <strong>{{entity_name}}</strong> on GigsFill.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">If you accept, you will have full access to create, edit, and manage gigs for <strong>{{entity_name}}</strong>.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 0 auto;">
<tr>
<td style="padding-right: 12px;">
<a href="{{accept_url}}" style="display: inline-block; background: #16a34a; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Accept Invitation</a>
</td>
<td>
<a href="{{decline_url}}" style="display: inline-block; background: #ffffff; color: #dc2626; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600; border: 1px solid #dc2626;">Decline</a>
</td>
</tr>
</table>
<p style="margin: 24px 0 0 0; font-size: 13px; color: #9ca3af;">If you didn&#39;t expect this invitation, you can safely ignore this email.</p>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>'''
    },

    "email_verification": {
        "subject": "Verify your GigsFill email address",
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Verify your email address</h1>
<p style="margin: 0 0 20px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{user_name}},</p>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Click the button below to verify your GigsFill email address. This link expires in 72 hours.</p>
<div style="margin-bottom: 24px;">
<a href="{{verify_url}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 14px 32px; text-decoration: none; border-radius: 6px; font-size: 15px; font-weight: 600;">Verify Email Address</a>
</div>
<p style="margin: 0; font-size: 13px; color: #9ca3af;">If you didn&#39;t create a GigsFill account, you can safely ignore this email.</p>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>'''
    },

    "welcome": {
        "subject": 'Welcome to GigsFill',
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Welcome, {{user_name}}!</h1>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Thanks for joining GigsFill - the easiest way to connect artists with venues.</p>
<p style="margin: 0 0 8px 0; font-size: 14px; color: #6b7280;"><strong style="color: #111827;">Artists:</strong> Create your profile, browse gigs, and start booking.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;"><strong style="color: #111827;">Venues:</strong> Set up your space, post gigs, and find talented performers.</p>
<a href="https://gigsfill.com/app/user-profile.html" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Complete Your Profile</a>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>'''
    },

    "venue_gig_confirmation_reminder": {
        "subject": 'Gig reminder: {{venue_name}} on {{date}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #1d4ed8;">Upcoming Gig Reminder&#160;</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, this is a reminder about your upcoming gig at <strong>{{venue_name}}</strong>:</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Address</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_address_link}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}} &#8211; {{end_time}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Pay</td>
<td style="padding: 6px 0; font-size: 14px; color: #059669; font-weight: 600;">${{pay}}</td>
</tr>
{{#artist_type}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Type</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{artist_type}}</td>
</tr>{{/artist_type}}
{{#band_formats}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lineup</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{band_formats}}</td>
</tr>{{/band_formats}}
{{#styles}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Styles</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{styles}}</td>
</tr>{{/styles}}
<tr>
<td colspan="2" style="padding: 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; vertical-align: top;">Notes</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500; white-space: pre-wrap;">{{notes_to_artist}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 4px 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Capacity</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_capacity}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Arrival</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{arrival_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Bar Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{bar_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Food Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{food_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Stage</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{stage_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Sound</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{sound_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Engineer</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{engineer_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lighting</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{lighting_info}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}" style="display: inline-block; background: #1d4ed8; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View My Gigs</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_open_gig_4w": {
        "subject": 'Open gig available at {{venue_name}} on {{date}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #7c3aed;">Open Gig Available&#160;</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, <strong>{{venue_name}}</strong> has an open gig and you&#39;re invited to book it. Frequency limitations are lifted!</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fffbeb; border: 1px solid #fcd34d; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Address</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_address_link}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #d97706; font-weight: bold;">{{date}}</td>
</tr>
{{slots_html}}
<tr>
<td colspan="2" style="padding: 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; vertical-align: top;">Notes</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500; white-space: pre-wrap;">{{notes_to_artist}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 4px 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Capacity</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_capacity}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Arrival</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{arrival_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Bar Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{bar_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Food Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{food_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Stage</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{stage_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Sound</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{sound_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Engineer</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{engineer_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lighting</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{lighting_info}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}" style="display: inline-block; background: #7c3aed; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Book This Gig</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_open_gig_2w": {
        "subject": 'Reminder: Open gig at {{venue_name}} on {{date}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #d97706;">Still Open - Book Now&#160;</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, <strong>{{venue_name}}</strong> still has an open gig. Don&#39;t miss your chance to book it!</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fffbeb; border: 1px solid #fcd34d; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Address</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_address_link}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #d97706; font-weight: bold;">{{date}}</td>
</tr>
{{slots_html}}
<tr>
<td colspan="2" style="padding: 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; vertical-align: top;">Notes</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500; white-space: pre-wrap;">{{notes_to_artist}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 4px 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Capacity</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_capacity}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Arrival</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{arrival_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Bar Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{bar_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Food Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{food_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Stage</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{stage_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Sound</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{sound_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Engineer</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{engineer_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lighting</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{lighting_info}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}" style="display: inline-block; background: #d97706; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Book This Gig</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_open_gig_1w": {
        "subject": 'Last chance: Open gig at {{venue_name}} on {{date}}!',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #dc2626;">Last Chance to Book!&#160;</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, <strong>{{venue_name}}</strong> has an open gig coming up very soon and still needs an artist. Frequency limitations have been lifted, so book it before somebody else does!</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fef2f2; border: 1px solid #fca5a5; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Address</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_address_link}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #dc2626; font-weight: bold;">{{date}}</td>
</tr>
{{slots_html}}
<tr>
<td colspan="2" style="padding: 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; vertical-align: top;">Notes</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500; white-space: pre-wrap;">{{notes_to_artist}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 4px 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Capacity</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_capacity}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Arrival</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{arrival_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Bar Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{bar_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Food Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{food_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Stage</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{stage_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Sound</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{sound_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Engineer</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{engineer_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lighting</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{lighting_info}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}&amp;blast_token={{blast_token}}" style="display: inline-block; background: #dc2626; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Book This Gig Now</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_open_gig_36h": {
        "subject": "Gig Still Open - {{venue_name}} on {{date}}",
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody><tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #f59e0b;">Gig Still Open</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, this gig at <strong>{{venue_name}}</strong> is still open and starts in about 36 hours. Book it before someone else does!</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; margin-bottom: 24px;">
<tbody><tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%"><tbody>
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td></tr>
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Address</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_address_link}}</td></tr>
{{#title}}<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td></tr>{{/title}}
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td></tr>
{{slots_html}}
<tr><td colspan="2" style="padding: 4px 0; border-top: 1px solid #e5e7eb;"></td></tr>
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Capacity</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_capacity}}</td></tr>
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Arrival</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{arrival_info}}</td></tr>
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Stage</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{stage_info}}</td></tr>
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Sound</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{sound_info}}</td></tr>
</tbody></table>
</td></tr></tbody></table>
<div style="background: #fef3c7; border: 1px solid #f59e0b; border-radius: 6px; padding: 12px 16px; margin-bottom: 24px; font-size: 13px; color: #92400e;">
  &#9889; <strong>Frequency limits and Preferred status requirements are waived</strong> - any artist can book this gig!
</div>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&open_gig={{gig_id}}&blast_token={{blast_token}}" style="display: inline-block; background: #f59e0b; color: #ffffff; padding: 14px 32px; text-decoration: none; border-radius: 6px; font-size: 15px; font-weight: 600;">Book This Gig Now &#8594;</a>
</td></tr>
<tr><td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td></tr>
</tbody></table>
</td></tr></tbody></table>'''
    },


    "venue_payment_charged": {
        "subject": 'Payment processed - {{artist_name}} gig on {{date}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Payment Processed</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{venue_name}}, your card has been charged for <strong>{{artist_name}}</strong>'s gig. Here are the details:</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Artist</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{artist_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{slot_times}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Performance Fee</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">${{pay}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Platform Fee</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">${{venue_fee}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 8px 0 0 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; font-weight: 600;">Total Charged</td>
<td style="padding: 6px 0; font-size: 16px; color: #dc2626; font-weight: bold;">${{total_charged}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">The artist has been notified and will receive their payment within 1-2 business days.</p>
<a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View Calendar</a>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "artist_payment_sent": {
        "subject": 'Payment sent - {{venue_name}} gig on {{date}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">&#128184; Payment Sent!</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Great news, {{artist_name}}! Your payment for the gig at {{venue_name}} has been transferred to your Stripe account and should arrive in your bank within 1&#8211;2 business days.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 120px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{slot_times}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Gig Pay</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">${{pay}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Platform Fee</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">${{artist_fee}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 8px 0 0 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; font-weight: 600;">Your Payout</td>
<td style="padding: 6px 0; font-size: 16px; color: #059669; font-weight: bold;">${{payout_amount}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<p style="margin: 0; font-size: 14px; color: #6b7280;">Funds should appear in your connected bank account within 1-2 business days.</p>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "artist_venue_payment_issue": {
        "subject": 'Payment issue with {{venue_name}} - your gig on {{date}}',
        "body": '''<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td align="center" style="padding: 20px;">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
<tbody>
<tr>
<td style="background-color: #1a1f2e; padding: 24px; text-align: center;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 24px;">
<h2 style="margin: 0 0 16px 0; font-size: 18px; color: #dc2626;">&#9888;&#65039; Payment Issue</h2>
<p style="margin: 0 0 16px 0; font-size: 14px; color: #374151; line-height: 1.6;">Hi {{artist_name}},</p>
<p style="margin: 0 0 16px 0; font-size: 14px; color: #374151; line-height: 1.6;">We&#39;re reaching out because <strong>{{venue_name}}</strong> no longer has a valid payment method on file with GigsFill. Your booked gig on <strong>{{date}}</strong> ({{slot_times}}) may be affected.</p>
<div style="background: #fef2f2; border: 1px solid #fca5a5; border-radius: 8px; padding: 16px; margin: 16px 0;">
<p style="margin: 0; color: #991b1b; font-size: 14px; line-height: 1.6;">We&#39;ve notified the venue and are working to resolve this. Your gig is still on the calendar. If the venue does not resolve the payment issue, we will notify you of any changes.</p>
</div>
<p style="margin: 16px 0 0 0; font-size: 14px; color: #6b7280;">- The GigsFill Team</p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_contract_sign_needed": {
        "subject": '{{artist_name}} has booked a gig - sign the contract to confirm',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Sign the contract to confirm</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;"><strong>{{artist_name}}</strong> has booked a gig at <strong>{{venue_name}}</strong> on {{date}} ({{slot_times}}). Please countersign the contract to confirm the booking.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">Until you sign, the gig is not officially confirmed and booked.</p>
<a href="https://gigsfill.com/app/venue-create-gigs.html?venue_id={{venue_id}}" style="display: inline-block; background: #1a1a2e; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View Calendar &amp; Countersign</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "venue_message_to_artists": {
        "subject": 'Message from {{venue_name}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">&#160;</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 2px 0px; font-size: 14px; color: rgb(107, 114, 128); width: 100px;">From:</td>
<td style="padding: 2px 0px; font-size: 14px; color: rgb(17, 24, 39); font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 2px 0px; font-size: 14px; color: rgb(107, 114, 128);">To:</td>
<td style="padding: 2px 0px; font-size: 14px; color: rgb(17, 24, 39); font-weight: 500;">{{artist_name}}</td>
</tr>
<tr>
<td style="padding: 2px 0px; font-size: 14px; color: rgb(107, 114, 128);">Subject:</td>
<td style="padding: 2px 0px; font-size: 14px; color: rgb(17, 24, 39); font-weight: 500;">{{subject}}</td>
</tr>
</tbody>
</table>
<div style="background: #f3f4f6; border-radius: 6px; padding: 20px; font-size: 14px; line-height: 1.6; color: #374151;">{{body}}</div>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill Support</p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "support_ticket": {
        "subject": '[GigsFill Support #{{ticket_id}}] {{subject}}',
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
<span style="font-size: 14px; color: #6b7280; margin-left: 12px;">Support Ticket #{{ticket_id}}</span>
</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">New Support Ticket</h1>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin-bottom: 24px;">
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 100px;">From:</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{user_name}} ({{user_email}})</td></tr>
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Category:</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{category}}</td></tr>
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Subject:</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{subject}}</td></tr>
</table>
<div style="background: #f3f4f6; border-radius: 6px; padding: 20px; font-size: 14px; line-height: 1.6; color: #374151;">
{{description}}
</div>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill Support</p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>'''
    },

    "recommend_gigsfill": {
        "subject": '{{user_name}} thinks you should check out GigsFill!',
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">You&#39;ve been recommended! &#127926;</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi{{recipient_greeting}}! <strong>{{user_name}}</strong> is using GigsFill and thought you&#39;d love it too.</p>
{{personal_note}}
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">GigsFill connects <strong>musicians</strong> with <strong>venues</strong> to make booking gigs simple, fast, and hassle-free. Whether you&#39;re an artist looking for your next gig or a venue searching for the perfect act - GigsFill has you covered.</p>
<div style="text-align: center; margin: 32px 0;">
<a href="https://gigsfill.com" style="display: inline-block; background: #06b6d4; color: #ffffff; padding: 14px 32px; text-decoration: none; border-radius: 6px; font-size: 15px; font-weight: 600;">Check Out GigsFill</a>
</div>
<p style="margin: 0; font-size: 13px; color: #9ca3af; text-align: center;">Free to sign up &middot; No commitment required</p>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>'''
    },

    "transfer_failed_artist": {
        "subject": 'Payment update - {{venue_name}} gig on {{date}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Payment Update</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, we have an update on your gig payment.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fefce8; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 120px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}} &#8211; {{end_time}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 8px 0 0 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; font-weight: 600;">Your Payout</td>
<td style="padding: 6px 0; font-size: 16px; color: #059669; font-weight: bold;">${{payout_amount}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">The venue was successfully charged but the transfer to you failed.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">The GigsFill team is working on this issue and you will receive your payment as soon as possible.&#160; Make sure your payment account is setup and verified in the Payments tab.</p>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "transfer_failed_venue": {
        "subject": 'Payment update - {{artist_name}} gig on {{date}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">Payment Update</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi there, we have an update regarding your recent gig payment.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fefce8; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 120px;">Artist</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{artist_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Time</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{start_time}} &#8211; {{end_time}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 8px 0 0 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; font-weight: 600;">You Were Charged</td>
<td style="padding: 6px 0; font-size: 16px; color: #dc2626; font-weight: bold;">${{venue_charge}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Artist Payout</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">${{payout_amount}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">You were charged <strong>${{venue_charge}}</strong> but the transfer to the artist failed.&#160; This usually means the artist&#39;s payment method is not correct.</p>
<p style="margin: 0 0 24px 0; font-size: 14px; color: #6b7280;">The GigsFill team is working on this issue and the artist will receive their payment as soon as possible.</p>
</td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "cancelled_gig_preferred_blast": {
        "subject": '🎵 Gig just opened up at {{venue_name}} on {{date}}!',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #f59e0b;">Gig Just Opened Up!&#160;</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, a gig at <strong>{{venue_name}}</strong> just became available after a cancellation. As a preferred artist, you have first access to book it - no frequency limitation!</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fffbeb; border: 1px solid #fcd34d; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Address</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_address_link}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #d97706; font-weight: bold;">{{date}}</td>
</tr>
{{slots_html}}
<tr>
<td colspan="2" style="padding: 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; vertical-align: top;">Notes</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500; white-space: pre-wrap;">{{notes_to_artist}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 4px 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Capacity</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_capacity}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Arrival</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{arrival_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Bar Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{bar_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Food Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{food_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Stage</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{stage_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Sound</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{sound_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Engineer</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{engineer_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lighting</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{lighting_info}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}&amp;blast_token={{blast_token}}" style="display: inline-block; background: #f59e0b; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Book This Gig Now</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "cancelled_gig_radius_blast": {
        "subject": '🎵 Last-minute gig available near you - {{venue_name}} on {{date}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #dc2626;">Last-Minute Gig Near You!&#160;</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, <strong>{{venue_name}}</strong> has an urgent opening less than 36 hours away. This gig is within {{radius_miles}} miles of you - you do not need preferred status to book this gig!</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fef2f2; border: 1px solid #fca5a5; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Address</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_address_link}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #dc2626; font-weight: bold;">{{date}}</td>
</tr>
{{slots_html}}
{{#band_formats}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lineup</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{band_formats}}</td>
</tr>{{/band_formats}}
{{#styles}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Styles</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{styles}}</td>
</tr>{{/styles}}
<tr>
<td colspan="2" style="padding: 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; vertical-align: top;">Notes</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500; white-space: pre-wrap;">{{notes_to_artist}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 4px 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Capacity</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_capacity}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Arrival</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{arrival_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Bar Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{bar_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Food Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{food_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Stage</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{stage_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Sound</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{sound_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Engineer</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{engineer_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lighting</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{lighting_info}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&amp;open_gig={{gig_id}}&amp;blast_token={{blast_token}}" style="display: inline-block; background: #dc2626; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">Book This Gig Now</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="../" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "artist_gig_edited": {
        "subject": 'Gig updated at {{venue_name}} on {{date}}',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody>
<tr>
<td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr>
<td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height: 40px; width: 160px; max-width: 160px; display: block; border: 0; outline: none;"></td>
</tr>
<tr>
<td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #f59e0b;">Gig Updated</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, <strong>{{venue_name}}</strong> has made changes to your upcoming gig. Here are the updated details:</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 6px; margin-bottom: 24px;">
<tbody>
<tr>
<td style="padding: 20px;"><br>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tbody>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Address</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_address_link}}</td>
</tr>
{{#title}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Title</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{title}}</td>
</tr>{{/title}}
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Date</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td>
</tr>
{{slots_html}}
{{#band_formats}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lineup</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{band_formats}}</td>
</tr>{{/band_formats}}
{{#styles}}<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Styles</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{styles}}</td>
</tr>{{/styles}}
<tr>
<td colspan="2" style="padding: 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280; vertical-align: top;">Notes</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500; white-space: pre-wrap;">{{notes_to_artist}}</td>
</tr>
<tr>
<td colspan="2" style="padding: 4px 0; border-top: 1px solid #e5e7eb;"></td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Capacity</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_capacity}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Arrival</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{arrival_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Bar Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{bar_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Food Tab</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{food_tab}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Stage</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{stage_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Sound</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{sound_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Engineer</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{engineer_info}}</td>
</tr>
<tr>
<td style="padding: 6px 0; font-size: 14px; color: #6b7280;">Lighting</td>
<td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{lighting_info}}</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>
<a href="https://gigsfill.com/app/artist-book-gigs.html?artist_id={{artist_id}}&open_gig={{gig_id}}" style="display: inline-block; background: #f59e0b; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-size: 14px; font-weight: 600;">View Updated Gig</a></td>
</tr>
<tr>
<td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td>
</tr>
</tbody>
</table>
</td>
</tr>
</tbody>
</table>'''
    },

    "waitlist_gig_available": {
        "subject": "🎵 A gig you're waitlisted for just opened up at {{venue_name}}!",
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody><tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;"><img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;"></td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #8b5cf6;">Waitlist Spot Available!</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi {{artist_name}}, a gig you were waitlisted for at <strong>{{venue_name}}</strong> has just opened up. Book it now before someone else does!</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa; border-radius: 6px; margin-bottom: 24px;">
<tbody><tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%"><tbody>
<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;width:130px;">Venue</td><td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{{venue_name}}</td></tr>
<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;">Date</td><td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{{date}}</td></tr>
<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;">Time</td><td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{{start_time}}{{end_time}}</td></tr>
<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;">Pay</td><td style="padding:6px 0;font-size:14px;color:#059669;font-weight:600;">${{pay}}</td></tr>
<tr><td style="padding:6px 0;font-size:14px;color:#6b7280;">Type</td><td style="padding:6px 0;font-size:14px;color:#111827;font-weight:500;">{{artist_type}}</td></tr>
</tbody></table>
</td></tr></tbody></table>
<div style="margin:24px 0;">
<a href="{{booking_url}}" style="display:inline-block;padding:14px 32px;background:#8b5cf6;color:#ffffff;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;">Book This Gig Now</a>
</div>
<p style="margin:8px 0 0;font-size:13px;color:#9ca3af;">Act fast - this gig is now open to all eligible artists. You received this because you joined the waitlist.</p>
</td></tr>
</tbody></table>
</td></tr></tbody></table>'''
    },

    "waitlist_offer": {
        "subject": "🎤 A gig just opened up for you at {{venue_name}}!",
        "body": '''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="display:block;border:0;">
</td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 16px 0; font-size: 22px; font-weight: 600; color: #111827;">A Gig Just Opened Up!</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">Hi <strong>{{artist_name}}</strong> &#8212; you were on the waitlist at <strong>{{venue_name}}</strong> and a slot just opened up! You have until <strong>{{offer_deadline}}</strong> to claim it before it goes to the next artist.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f9fafb; border-radius: 6px; margin-bottom: 24px;">
<tr><td style="padding: 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Venue</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{venue_name}}</td></tr>
<tr><td style="padding: 6px 0; font-size: 14px; color: #6b7280; width: 130px;">Date</td><td style="padding: 6px 0; font-size: 14px; color: #111827; font-weight: 500;">{{date}}</td></tr>
{{slots_html}}
</table>
</td></tr>
</table>
<div style="margin: 24px 0; display: flex; gap: 12px; flex-wrap: wrap;">
<a href="{{book_url}}" style="display:inline-block;padding:14px 32px;background:#059669;color:#ffffff;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;">&#10003; Book This Gig</a>
<a href="{{decline_url}}" style="display:inline-block;padding:14px 32px;background:#f3f4f6;color:#374151;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;">&#10005; Not Available</a>
</div>
<p style="margin: 8px 0 0; font-size: 13px; color: #9ca3af;">This exclusive offer expires in {{expires_hours}}. After that, it goes to the next artist on the waitlist.</p>
</td></tr>
<tr><td style="padding: 20px 40px; background: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #6b7280; text-decoration: none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>''',
    },

    "support_ticket_received": {
        "subject": "[GigsFill Support #{{ticket_id}}] {{subject}}",
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color:#f8f9fa;">
<tr><td style="padding:40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:560px;margin:0 auto;background-color:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td></tr>
<tr><td style="padding:32px 40px;">
<h1 style="margin:0 0 16px;font-size:20px;font-weight:600;color:#059669;">We got your message!</h1>
<p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#4b5563;">Hi <strong>{{user_name}}</strong> - thanks for reaching out. We&#39;ve received your support request and will get back to you as soon as possible.</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background:#f0fdf4;border:1px solid #86efac;border-radius:6px;margin-bottom:24px;">
<tr><td style="padding:20px;">
<table role="presentation" width="100%">
<tr>
<td style="padding:5px 0;font-size:13px;color:#6b7280;width:100px;">Ticket #</td>
<td style="padding:5px 0;font-size:14px;color:#111827;font-weight:600;">{{ticket_id}}</td>
</tr>
<tr>
<td style="padding:5px 0;font-size:13px;color:#6b7280;">Category</td>
<td style="padding:5px 0;font-size:14px;color:#111827;font-weight:500;">{{category}}</td>
</tr>
<tr>
<td style="padding:5px 0;font-size:13px;color:#6b7280;vertical-align:top;">Subject</td>
<td style="padding:5px 0;font-size:14px;color:#111827;font-weight:500;">{{subject}}</td>
</tr>
</table>
</td></tr>
</table>
<div style="background:#f3f4f6;border-radius:6px;padding:16px;margin-bottom:24px;font-size:14px;line-height:1.6;color:#374151;white-space:pre-wrap;">{{description}}</div>
<p style="margin:0 0 16px;font-size:14px;color:#4b5563;">You&#39;ll receive an email when we reply. You can also view and respond to this ticket at any time using the link below.</p>
<div style="margin:0 0 8px;">
<a href="{{reply_url}}" style="display:inline-block;background:#059669;color:#ffffff;padding:12px 28px;text-decoration:none;border-radius:6px;font-size:14px;font-weight:600;">View My Ticket</a>
</div>
</td></tr>
<tr><td style="padding:20px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color:#6b7280;text-decoration:none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>''',
    },

    "support_ticket_reply": {
        "subject": "Re: [GigsFill Support #{{ticket_id}}] {{ticket_subject}}",
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color:#f8f9fa;">
<tr><td style="padding:40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:560px;margin:0 auto;background-color:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td></tr>
<tr><td style="padding:32px 40px;">
<h1 style="margin:0 0 8px;font-size:20px;font-weight:600;color:#0ea5e9;">Support Reply</h1>
<p style="margin:0 0 4px;font-size:14px;color:#6b7280;">Ticket #{{ticket_id}} &middot; {{ticket_subject}}</p>
<p style="margin:16px 0 20px;font-size:15px;line-height:1.6;color:#4b5563;">Hi <strong>{{user_name}}</strong>,</p>
<div style="background:#e0f2fe;border-radius:6px;padding:16px;margin:0 0 20px;font-size:14px;line-height:1.6;color:#1e293b;white-space:pre-wrap;">{{reply_body}}</div>
<p style="font-size:13px;color:#6b7280;margin:0 0 20px;">- {{admin_name}}</p>
{{#previous_thread}}<hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0 16px;">
<p style="font-size:12px;color:#9ca3af;margin:0 0 8px;">Previous messages:</p>
{{previous_thread}}
{{/previous_thread}}
<hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0 16px;">
<p style="font-size:12px;color:#9ca3af;margin:0 0 8px;">Original ticket:</p>
<div style="background:#f3f4f6;border-radius:6px;padding:12px 16px;font-size:13px;color:#374151;line-height:1.5;">
<div style="font-size:11px;color:#6b7280;margin-bottom:4px;"><strong>{{user_name}}</strong> &middot; {{category}}</div>
<div>{{description}}</div>
</div>
<div style="margin:24px 0 8px;">
<a href="{{reply_url}}" style="display:inline-block;background:#0ea5e9;color:#ffffff;padding:12px 28px;text-decoration:none;border-radius:6px;font-size:14px;font-weight:600;">View &amp; Reply</a>
</div>
</td></tr>
<tr><td style="padding:20px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color:#6b7280;text-decoration:none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>''',
    },


    "support_ticket_admin_notification": {
        "subject": "[GigsFill Support #{{ticket_id}}] {{subject}}",
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color:#f8f9fa;">
<tr><td style="padding:40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width:560px;margin:0 auto;background-color:#ffffff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding:32px 40px 24px;border-bottom:1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td></tr>
<tr><td style="padding:32px 40px;">
<h1 style="margin:0 0 6px;font-size:20px;font-weight:600;color:#f59e0b;">&#128276; New Support Ticket</h1>
<p style="margin:0 0 20px;font-size:13px;color:#6b7280;">Ticket #{{ticket_id}} &middot; submitted {{submitted_at}}</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background:#fffbeb;border:1px solid #fcd34d;border-radius:6px;margin-bottom:20px;">
<tr><td style="padding:20px;">
<table role="presentation" width="100%">
<tr>
<td style="padding:5px 0;font-size:13px;color:#92400e;width:90px;font-weight:600;">From</td>
<td style="padding:5px 0;font-size:14px;color:#111827;font-weight:600;">{{user_name}}</td>
</tr>
<tr>
<td style="padding:5px 0;font-size:13px;color:#92400e;font-weight:600;">Email</td>
<td style="padding:5px 0;font-size:14px;color:#111827;">{{user_email}}</td>
</tr>
<tr>
<td style="padding:5px 0;font-size:13px;color:#92400e;font-weight:600;">Category</td>
<td style="padding:5px 0;font-size:14px;color:#111827;">{{category}}</td>
</tr>
<tr>
<td style="padding:5px 0;font-size:13px;color:#92400e;font-weight:600;vertical-align:top;">Subject</td>
<td style="padding:5px 0;font-size:14px;color:#111827;font-weight:600;">{{subject}}</td>
</tr>
</table>
</td></tr>
</table>
<p style="margin:0 0 8px;font-size:12px;color:#9ca3af;text-transform:uppercase;letter-spacing:.05em;font-weight:600;">Message</p>
<div style="background:#f3f4f6;border-radius:6px;padding:16px;font-size:14px;line-height:1.6;color:#374151;">{{description}}</div>
<div style="margin:24px 0 8px;">
<a href="{{admin_url}}" style="display:inline-block;background:#f59e0b;color:#ffffff;padding:12px 28px;text-decoration:none;border-radius:6px;font-size:14px;font-weight:600;">View &amp; Reply in Admin</a>
</div>
</td></tr>
<tr><td style="padding:20px 40px;background:#f8f9fa;border-top:1px solid #eee;">
<p style="margin:0;color:#6b7280;font-size:12px;text-align:center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color:#6b7280;text-decoration:none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>''',
    },

    "new_gigs_batch_blast": {
        "subject": '🎵 New Gigs Available at {{venue_name}}!',
        "body": '''<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tbody><tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tbody>
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;display:block;border:0;">
</td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 8px 0; font-size: 22px; font-weight: 600; color: #f59e0b;">New Gigs Available!</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">
Hi {{artist_name}}, <strong>{{venue_name}}</strong> has just added new gig dates and you're invited to book one that works for you!
</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #fffbeb; border: 1px solid #fcd34d; border-radius: 6px; margin-bottom: 24px;">
<tbody><tr><td style="padding: 20px;">
<p style="margin: 0 0 12px 0; font-size: 13px; font-weight: 700; color: #92400e; text-transform: uppercase; letter-spacing: 0.05em;">Available Dates</p>
{{gigs_list_html}}
<tr><td colspan="2" style="padding: 12px 0 4px 0; border-top: 1px solid #fcd34d;">
<p style="margin:0;font-size:13px;color:#6b7280;">Venue: <strong style="color:#111827;">{{venue_name}}</strong></p>
{{#venue_address}}<p style="margin:4px 0 0;font-size:13px;color:#6b7280;">{{venue_address_link}}</p>{{/venue_address}}
</td></tr>
</td></tr></tbody>
</table>
<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin: 0 auto 24px;">
<tbody><tr><td style="background-color: #635bff; border-radius: 6px; text-align: center;">
<a href="{{booking_url}}" style="display:inline-block;padding:14px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;">View &amp; Book Gigs</a>
</td></tr></tbody>
</table>
<p style="margin: 0; font-size: 13px; color: #6b7280; line-height: 1.6;">
You're receiving this because you're a preferred artist at {{venue_name}} or within their blast radius. Book fast - these fill up quickly!
</p>
</td></tr>
<tr><td style="padding: 20px 40px; background-color: #f9fafb; border-top: 1px solid #eee; border-radius: 0 0 8px 8px;">
<p style="margin: 0; font-size: 12px; color: #9ca3af; text-align: center;">GigsFill &middot; <a href="https://gigsfill.com" style="color:#9ca3af;">gigsfill.com</a></p>
</td></tr>
</tbody></table>
</td></tr></tbody></table>''',
    },


    "venue_review_request": {
        "subject": "How was {{artist_name}}? Leave a review &#11088;",
        "body": '''
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 8px 0; font-size: 22px; font-weight: 600; color: #111827;">How was {{artist_name}}? &#11088;</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">
  Hi <strong>{{venue_name}}</strong> - your gig <strong>{{gig_title}}</strong> on <strong>{{gig_date}}</strong> is complete. Leave a quick review to help other venues discover great talent on GigsFill.
</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0">
<tr><td style="padding-bottom: 20px;">
<a href="{{review_url}}" style="display:inline-block;background:#f59e0b;color:#ffffff;padding:13px 28px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:700;">&#11088; Leave a Review</a>
</td></tr>
</table>
<p style="margin: 0; font-size: 12px; color: #9ca3af;">Reviews help artists grow their reputation and make GigsFill better for everyone.</p>
</td></tr>
<tr><td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>
''',
    },

    "artist_review_request": {
        "subject": "How was {{venue_name}}? Leave a review &#11088;",
        "body": '''
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #f8f9fa;">
<tr><td style="padding: 40px 20px;">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="max-width: 560px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);">
<tr><td style="padding: 32px 40px 24px 40px; border-bottom: 1px solid #eee;">
<img src="https://gigsfill.com/app/static/img/gigsfill-logo_light.png" alt="GigsFill" width="160" height="40" style="height:40px;width:160px;max-width:160px;display:block;border:0;outline:none;">
</td></tr>
<tr><td style="padding: 32px 40px;">
<h1 style="margin: 0 0 8px 0; font-size: 22px; font-weight: 600; color: #111827;">How was {{venue_name}}? &#11088;</h1>
<p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #4b5563;">
  Hi <strong>{{artist_name}}</strong> - your gig <strong>{{gig_title}}</strong> at <strong>{{venue_name}}</strong> on <strong>{{gig_date}}</strong> is complete. Leave a quick review to share your experience with this venue.
</p>
<table role="presentation" cellspacing="0" cellpadding="0" border="0">
<tr><td style="padding-bottom: 20px;">
<a href="{{review_url}}" style="display:inline-block;background:#06b6d4;color:#ffffff;padding:13px 28px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:700;">&#11088; Leave a Review</a>
</td></tr>
</table>
<p style="margin: 0; font-size: 12px; color: #9ca3af;">Your review helps other artists find great venues on GigsFill.</p>
</td></tr>
<tr><td style="padding: 24px 40px; background-color: #f8f9fa; border-top: 1px solid #eee;">
<p style="margin: 0; color: #6b7280; font-size: 12px; text-align: center;">&copy; 2026 GigsFill &middot; <a href="https://gigsfill.com" style="color: #1a1a2e; text-decoration: none;">gigsfill.com</a></p>
</td></tr>
</table>
</td></tr>
</table>
''',
    },

}

def run_migration():
    """Populate email templates in database"""
    from backend.db import get_db_connection as _get_conn, _IS_POSTGRES
    conn = _get_conn()
    cursor = conn.cursor()

    # Check if table exists (syntax differs between SQLite and PostgreSQL)
    if _IS_POSTGRES:
        cursor.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name='email_templates'"
        )
    else:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='email_templates'")
    if not cursor.fetchone():
        cursor.execute("""
            CREATE TABLE email_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_type TEXT UNIQUE NOT NULL,
                template_key TEXT,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    
    # Check columns
    cursor.execute("PRAGMA table_info(email_templates)")
    columns = [col[1] for col in cursor.fetchall()]
    key_column = 'notification_type' if 'notification_type' in columns else 'template_key'
    
    for notification_type, template in TEMPLATES.items():
        cursor.execute(f"SELECT id FROM email_templates WHERE {key_column} = ?", (notification_type,))
        existing = cursor.fetchone()
        
        if existing:
            # Skip overwriting templates that have been manually customized with slots_html
            existing_body = cursor.execute(
                f"SELECT body FROM email_templates WHERE {key_column} = ?", (notification_type,)
            ).fetchone()
            if existing_body and '{{slots_html}}' in existing_body[0]:
                continue  # Preserve manually customized template
            cursor.execute(f"""
                UPDATE email_templates SET subject = ?, body = ?, updated_at = CURRENT_TIMESTAMP
                WHERE {key_column} = ?
            """, (template['subject'], template['body'], notification_type))
        else:
            try:
                cursor.execute("""
                    INSERT INTO email_templates (template_key, notification_type, subject, body)
                    VALUES (?, ?, ?, ?)
                """, (notification_type, notification_type, template['subject'], template['body']))
            except:
                cursor.execute(f"""
                    INSERT INTO email_templates ({key_column}, subject, body)
                    VALUES (?, ?, ?)
                """, (notification_type, template['subject'], template['body']))
    
    conn.commit()
    conn.close()
    logger.info(f"Email templates populated ({len(TEMPLATES)} templates)")

if __name__ == "__main__":
    run_migration()
