# Asset attribution

## satellite.svg / sat-grey.png / sat-yellow.png / sat-green.png

Derived from "Satellite" by The Noun Project, licensed under
**CC BY 3.0** (https://creativecommons.org/licenses/by/3.0/).

Source: https://upload.wikimedia.org/wikipedia/commons/a/a9/Satellite_%283485%29_-_The_Noun_Project.svg

The original black-on-transparent glyph was recoloured to white
(`satellite.svg`) and rasterised to 48x48 RGBA PNGs tinted per GPS state:
grey (no GPS device), yellow (#ffd24d, no fix), green (#6ee06e, fix).
The grey "no device" icon (`sat-grey.png`) carries a red X so a dead state
reads at a glance instead of fading into the dark status bar.

## wifi.svg / wifi-grey.png / wifi-yellow.png / wifi-green.png

From "Wifiservice.svg" on Wikimedia Commons, released into the
**public domain (CC0)** by its author.

Source: https://commons.wikimedia.org/wiki/File:Wifiservice.svg

Rasterised to 48x48 RGBA PNGs tinted per network state: green (#6ee06e,
connected with a default route), yellow (#ffd24d, link up but no default
route), grey (#8a8a8a, interface down / Wi-Fi dropped). The grey icon carries
a red X for the same at-a-glance reason as the satellite icon.
