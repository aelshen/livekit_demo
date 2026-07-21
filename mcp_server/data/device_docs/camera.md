# Camera Troubleshooting

## Camera shows "offline"
1. Check the camera's LED — solid blue means connected, no light means no power.
2. Confirm the camera is within Wi-Fi range; cameras are more range-sensitive than other devices due to continuous streaming.
3. Power-cycle by unplugging for 10 seconds.
4. If wired (PoE), check the switch port and cable for damage.

## No cloud recordings / gaps in the timeline
1. Check subscription status — recording history length and retention depend on the subscription tier; a lapsed subscription stops new cloud uploads but keeps existing footage until it expires.
2. Confirm "Continuous Recording" vs "Motion-only Recording" mode — gaps between events are expected in motion-only mode.
3. Local storage (SD card, if present) fills up and overwrites oldest footage first; cloud storage does not.

## Motion alerts not firing
1. Check notification settings in the app aren't muted for that camera.
2. Adjust motion sensitivity — very low sensitivity can miss distant or fast motion.
3. Activity zones (if configured) may be excluding the area where motion occurred.

## Escalate to a human agent if
- Live view fails on multiple devices/networks (points to a backend/service issue, not the camera itself).
- Customer reports unauthorized access to their camera feed — treat as a security incident and escalate immediately.
