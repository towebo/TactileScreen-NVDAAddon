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



class LocalBrightnessProcessor:
	"""Reusable brightness and integral-image buffers for local thresholding."""

	def __init__(self, width: int, height: int):
		self.width = width
		self.height = height
		self.brightness = [0] * (width * height)
		# One extra row and column simplify summed-area lookups.
		self.integralStride = width + 1
		self.integral = [0] * ((width + 1) * (height + 1))

	def update(self, image) -> None:
		"""Convert RGB pixels to brightness and rebuild the summed-area table."""
		width = self.width
		stride = self.integralStride
		brightness = self.brightness
		integral = self.integral

		# The first integral row is always zero. The first entry of each
		# following row is also reset below.
		for x in range(stride):
			integral[x] = 0

		for y in range(self.height):
			rowSum = 0
			brightnessOffset = y * width
			integralRow = (y + 1) * stride
			previousIntegralRow = y * stride
			integral[integralRow] = 0

			for x in range(width):
				pixel = image[y][x]
				value = int(
					(0.3 * pixel.rgbBlue)
					+ (0.59 * pixel.rgbGreen)
					+ (0.11 * pixel.rgbRed)
				)
				brightness[brightnessOffset + x] = value
				rowSum += value
				integral[integralRow + x + 1] = (
					integral[previousIntegralRow + x + 1]
					+ rowSum
				)

	def isWhite(self, x: int, y: int, blur: int = 4) -> bool:
		"""Apply the original local-mean threshold using constant-time area sums."""
		left = max(0, x - blur)
		right = min(self.width, x + blur + 1)
		top = max(0, y - blur)
		bottom = min(self.height, y + blur + 1)

		stride = self.integralStride
		integral = self.integral
		regionSum = (
			integral[bottom * stride + right]
			- integral[top * stride + right]
			- integral[bottom * stride + left]
			+ integral[top * stride + left]
		)

		# Preserve the old edge behavior: samples outside the image count as
		# black (zero), so always divide by the complete kernel area.
		kernelWidth = (blur * 2) + 1
		threshold = regionSum / (kernelWidth * kernelWidth)
		return self.brightness[y * self.width + x] >= threshold
