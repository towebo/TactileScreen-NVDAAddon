# Tactile Screen NVDA Add-on

This addon mirrors the computer screen on a DotPad graphical area. It can also display multiline braille on the graphical area.

## Copyright and License
This add-on is copyright (C) 2026 MAWINGU.
This add-on is licensed under the GNU General Public License version 2.
The image processing is based on the DotPad prototype add-on created by Michael curran <mick@nvaccess.org>.

### Contact Information
This add-on hangs its hat at https://github.com/towebo/TactileScreen-NVDAAddon.
You can reach me at karl-otto@mawingu.se.

## Key Commands
- NVDA+control+Shift+f8: Open  connection dialog.
- NVDA+f8: Displays the screen at the upper left corner of the navigator object, pressed twice will track the navigator object.
- NVDA+Shift+f8: Displays the screen around the mouse pointer, pressed twice will track the mouse pointer.
- NVDA+Control+f8: Displays multiline braille on the graphical area.
- NVDA+Escape: Stops tracking and automatic refresh.

## Auto refresh
When in Screen Mirroring Display Mode the add-on refreshes what's mirrored from  the computer screen once per second. This is done regardless if navigator object or mouse tracking is active. To stop the auto refresh use the gesture assigned to Stop tracking and auto refresh.

## Zooming and panning
The physical keys on the DotPad is used to zoom and pan the part of the computer screen that's mirrored.
From left to right:
- Pan left
- Pan up
- Zoom in
- Zoom out
- Pan down
- Pan right

The coordinates and size of the viewport is displayed on the dedicated braille line below the graphical area as X Y Width Height. Each value has 5 cells each which is enough to include a space for most computer screens.

## Multiline Braille Navigation
When in multiline braille mode you can use the physical keys on the DotPad to navigate.
From left to right:
- Scroll  Back
-  Previous Line
- Not Used
- Not Used
- Next Line
- Scroll Forward

## Known Limitations
### Braille
This add-on doesn't implement a braille driver for its  multiline braille functionality and therefore it interfears with the braille display connected through NVDA when in Multiline Braille Display Mode. The DotPad X is able to display 8 rows with 20 braille cells on the graphical area and it reports this to NVDA and this will limit the characters shown on the connected braille display to 20 characters. If you have both a DotPad and a standard braille display connected  you'll probably have the DotPad in Screen Mirroring Display Mode where braille works as expected.
You should limit the number of characters in the browse mode to aprox 120 characters. Even if a DotPad X is able to display 160 characters space is wasted when wrapping words to avoid cutting them off when reaching end of line.
This add-on 's main purpose is screen mirroring and the multiline braille is just a useful bonus and does need more love to reach the potential that multiline braille has on a DotPad.

### Connectivity
Although the DotPad can be connected both via Bluetooth LE and USB this add-on only implmements connections via Bluetooth at this time.

## Image processing details
The add-on captures the part of the screen that matches the current zoom level and the coordinates of the navigator object, mouse pointer or the current panned position. The size of the view viewport is a multiple of the graphical area of the connected device.
If the image is expected to be black on white, it tries to keep black pixels at the expense of white pixels, and the opposite for white on black. this ensures that thin lines are not removed when shrinking. 
As the dotpad can only show a monochrome image (I.e. raised dots for black, no dots for white), a suitable threshold must be found to choose how bright something should be to be classed as white. The image processing currently uses a very basic local mean threshold approach where by the average brightness is calculated for  a  block of 7 by 7  pixels around the pixel in question, and then this value is used as the threshold. this approach ensures that changes can be shown even if lighting changes across the image.
**Note: It's often  better to avoid inverted colors   when screen mirroring because the tactile representation of text, lines and so forth is more distinct with black on whie.**
