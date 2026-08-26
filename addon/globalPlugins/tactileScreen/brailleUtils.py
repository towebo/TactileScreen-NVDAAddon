		# A part of Tactile Screen add-on
# Copyright (C) 2026 MAWINGU
# this code is licensed under the GNU General Public License version 2.

import os
import louis
import louisHelper
import brailleTables
import config

brailleCellWidth = 3

dot1 = 1
dot2 = 2
dot3 = 4
dot4 = 8
dot5 = 16
dot6 = 32
dot7 = 64
dot8 = 128


brailleDotCoords = [
	# dot1
	(0, 0),
	# dot2
	(0, 1),
	# dot3
	(0, 2),
	# dot4
	(1, 0),
	# dot5
	(1, 1),
	# dot6
	(1, 2),
	# dot7
	(0, 3),
	# dot8
	(1, 3),
]


def drawBrailleCells(drawFunc, x, y, cells):
	for cell in cells:
		for dot in range(0, 8):
			if 1 << dot & cell:
				dotX, dotY = brailleDotCoords[dot]
				drawFunc(x + dotX, y + dotY)
		x += 3

def wrapBrailleCells(cells, maxWidth):
	lines = []
	wrap_on_these = translateTextToBraille(" ,.!?")
	
	while cells:
		if len(cells) <= maxWidth:
			lines.append(cells)
			log.info("Does fit")
			break
		
		breakPos = None
		
		for i in range(maxWidth - 1, 0, -1):
			if cells[i - 1] in wrap_on_these:
				breakPos = i - 1
				break
		if breakPos is None:
			lines.append(cells[:maxWidth])
			cells = cells[maxWidth:]
		else:
			lines.append(cells[:breakPos])
			cells = cells[breakPos + 1:]

	return lines

def splitBrailleLines(cells, cellsPerLine):
	return [
		cells[i:i + cellsPerLine]
		for i in range(0, len(cells), cellsPerLine)
		]

def translateTextToBraille(text, brailleTable=None):
	if not brailleTable:
		brailleTable = config.conf["braille"]["translationTable"]
	return louisHelper.translate(
		[os.path.join(brailleTables.TABLES_DIR, brailleTable), "braille-patterns.cti"],
		text,
		mode=louis.dotsIO
	)[0]
