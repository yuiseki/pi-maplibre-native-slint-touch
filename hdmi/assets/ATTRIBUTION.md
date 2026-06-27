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

## kbd-green.png

From the **OOjs UI** "keyboard" icon (MediaWiki / Wikimedia), **MIT** licensed.

Source: https://commons.wikimedia.org/wiki/File:OOjs_UI_icon_keyboard.svg

The single-path glyph was filled green (#6ee06e) and rasterised to 48x48 RGBA.
Shown in the status bar only while a USB or Bluetooth keyboard is connected.

## battery-charge.png / battery-full.png / battery-high.png / battery-middle.png / battery-low.png

Flat battery icons by **Icons8** (https://icons8.com), via Wikimedia Commons.

Sources: https://commons.wikimedia.org/wiki/File:Icons8_flat_charge_battery.svg
(and the matching full / high / middle / low "Icons8_flat_*_battery.svg" files).

Rasterised to 48x48 RGBA. Selected by power state: charge when plugged in,
otherwise full/high/middle/low by battery percent.
