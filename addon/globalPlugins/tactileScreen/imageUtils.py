		# A part of Tactile Screen add-on
# Copyright (C) 2026 MAWINGU
# this code is licensed under the GNU General Public License version 2.

from enum import IntEnum
import ctypes
from winBindings import gdi32 as gdiDefs

SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class StretchMode(IntEnum):
	BLACKONWHITE = 1
	WHITEONBLACK = 2
	HALFTONE = 4

class ScreenCapture:
	"""Reusable GDI screen capture resources for one output size.

	An instance should be created, used, and closed on the same worker thread.
	"""

	def __init__(self, bufferWidth: int, bufferHeight: int):
		self.bufferWidth = bufferWidth
		self.bufferHeight = bufferHeight
		self.screenDC = None
		self.memDC = None
		self.memBitmap = None
		self.oldBitmap = None

		self.screenDC = user32.GetDC(0)
		if not self.screenDC:
			raise OSError("Could not get screen device context")

		try:
			self.memDC = gdi32.CreateCompatibleDC(self.screenDC)
			if not self.memDC:
				raise OSError("Could not create memory device context")

			self.memBitmap = gdi32.CreateCompatibleBitmap(
				self.screenDC,
				bufferWidth,
				bufferHeight,
			)
			if not self.memBitmap:
				raise OSError("Could not create capture bitmap")

			self.oldBitmap = gdi32.SelectObject(self.memDC, self.memBitmap)

			self.bitmapInfo = gdiDefs.BITMAPINFO()
			self.bitmapInfo.bmiHeader.biSize = ctypes.sizeof(
				self.bitmapInfo.bmiHeader
			)
			self.bitmapInfo.bmiHeader.biWidth = bufferWidth
			self.bitmapInfo.bmiHeader.biHeight = -bufferHeight
			self.bitmapInfo.bmiHeader.biPlanes = 1
			self.bitmapInfo.bmiHeader.biBitCount = 32
			self.bitmapInfo.bmiHeader.biCompression = BI_RGB

			# Allocate the pixel buffer once and reuse it for every frame.
			self.buffer = (
				(gdiDefs.RGBQUAD * bufferWidth)
				* bufferHeight
			)()
		except Exception:
			self.close()
			raise

	def capture(
		self,
		srcX: int,
		srcY: int,
		srcWidth: int,
		srcHeight: int,
		stretchMode: StretchMode = StretchMode.HALFTONE,
	):
		"""Capture and resize a screen rectangle into the reusable pixel buffer."""
		ratio = max(
			srcWidth / self.bufferWidth,
			srcHeight / self.bufferHeight,
		)
		destWidth = int(srcWidth / ratio)
		destHeight = int(srcHeight / ratio)
		destX = (self.bufferWidth - destWidth) // 2
		destY = (self.bufferHeight - destHeight) // 2

		gdi32.SetStretchBltMode(self.memDC, stretchMode)
		if not gdi32.StretchBlt(
			self.memDC,
			destX,
			destY,
			destWidth,
			destHeight,
			self.screenDC,
			srcX,
			srcY,
			srcWidth,
			srcHeight,
			SRCCOPY,
		):
			raise OSError("StretchBlt failed")

		if not gdi32.GetDIBits(
			self.memDC,
			self.memBitmap,
			0,
			self.bufferHeight,
			self.buffer,
			ctypes.byref(self.bitmapInfo),
			DIB_RGB_COLORS,
		):
			raise OSError("GetDIBits failed")

		return self.buffer, (destX, destY, destWidth, destHeight)

	def close(self) -> None:
		"""Release all GDI resources owned by this capture object."""
		if self.memDC and self.oldBitmap:
			gdi32.SelectObject(self.memDC, self.oldBitmap)
			self.oldBitmap = None

		if self.memBitmap:
			gdi32.DeleteObject(self.memBitmap)
			self.memBitmap = None

		if self.memDC:
			gdi32.DeleteDC(self.memDC)
			self.memDC = None

		if self.screenDC:
			user32.ReleaseDC(0, self.screenDC)
			self.screenDC = None


def findMeanBrightnessThreshold(image: ctypes.Array, x: int, y: int, blur: int=1):
	"""
	Calculates a suitable brightness threshold at which a pixel could be considered white in a monochrome image, using the mean brightness of the surrounding pixels.
	"""
	imageHeight = len(image)
	imageWidth = len(image[0])
	surroundingPixels = []
	left = int(x - blur)
	right = int(x + blur + 1)
	top = int(y - blur)
	bottom = int(y + blur + 1)
	for i in range(top, bottom):
		for j in range(left, right):
			if i < 0 or i >= imageHeight or j < 0 or j >= imageWidth:
				surroundingPixels.append(0)
			else:
				surroundingPixels.append(rgbPixelBrightness(image[i][j]))
	threshold = sum(surroundingPixels) / len(surroundingPixels)
	return threshold


def rgbPixelBrightness(p):
	"""Converts a RGBQUAD pixel in to  one grey-scale brightness value."""
	return int((0.3*p.rgbBlue)+(0.59*p.rgbGreen)+(0.11*p.rgbRed))

def getMonochromePixelUsingLocalBrightnessThreshold(image, x, y, blur=4):
	"""
	Fetches a monochrome pixel from an RGB image, using the local mean brightness to calculate a suitable brightness threshold.
	"""
	threshold = findMeanBrightnessThreshold(image, x, y, blur)
	px = rgbPixelBrightness(image[y][x])
	return px >= threshold
