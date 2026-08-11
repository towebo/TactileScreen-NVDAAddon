# Tactile Screen NVDA Add-on

This addon mirrors the computer screen on a DotPad graphical area.

# Copyright and License
This add-on is copyright (C) 2026 MAWINGU.
This add-on is licensed under the GNU General Public License version 2.
The image processing is based on the DotPad prototype add-on created by Michael curran <mick@nvaccess.org>.

## Key Commands
* control+NVDA+f8: Open  connection dialog.
* NVDA+f8: Displays the screen at the upper left corner of the navigator object, pressed twice will track the navigator object.
* shift+NvDA+f8: Displays the screen around the mouse pointer, pressed twice will track the mouse pointer.
* NVDA+Escape: Stops tracking and automatic refresh.

## Image processing details
The add-on captures the part of the screen that matches the current zoom level and the coordinates of the navigator object, mouse pointer or the current panned position. The size of the view viewport is a multiple of the graphical area of the connected device.
If the image is expected to be black on white, it tries to keep black pixels at the expense of white pixels, and the opposite for white on black. this ensures that thin lines are not removed when shrinking. 
As the dotpad can only show a monochrome image (I.e. raised dots for white, no dots for black), a suitable threshold must be found to choose how bright something should be to be classed as white. this add-on currently uses a very basic local mean threshold approach where by the average brightness is calculated for  a  block of 7 by 7  pixels around the pixel in question, and then this value is used as the threshold. this approach ensures that changes can be shown even if lighting changes across the image.

