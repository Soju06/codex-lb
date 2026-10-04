Port the fork warning to the existing upstream account list item. Use the existing nearest-expiry summary field and one timer for the list, with cleanup on unmount. For example, two credits expiring tomorrow gain a warning; unknown expiry does not. This is display-only and does not change redemption policy or the existing Reset action countdown.

Keep this change active for upstream review; sync and archive at merge as requested by the maintainer in #2065.
