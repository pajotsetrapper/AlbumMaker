"""Display copies and thumbnails (albummaker.imaging)."""

import io
import unittest

from PIL import Image

from .helpers import TempDirMixin

from albummaker import imaging


class DisplayJpegTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()

    def tiff_with_exif(self, xmp_size: int):
        exif = Image.Exif()
        exif[0x010F] = "Canon"                      # Make
        exif[0x0112] = 6                            # Orientation: needs rotating
        exif[700] = b"<x:xmpmeta>" + b" " * xmp_size + b"</x:xmpmeta>"
        exif[0x0132] = "2026:05:01 10:00:00"        # DateTime (Pillow's TIFF writer drops sub-IFDs)
        path = self.tmp / "export.tif"
        Image.new("RGB", (40, 20), (200, 10, 10)).save(path, exif=exif)
        return path

    def test_a_photo_with_exif_too_long_for_a_jpeg_is_still_converted(self):
        # Lightroom and Photoshop TIFF exports often carry 100 KB of XMP
        data = imaging.display_jpeg_bytes(self.tiff_with_exif(100_000))
        out = Image.open(io.BytesIO(data))
        self.assertEqual(out.size, (20, 40))        # rotated upright
        exif = out.getexif()
        self.assertEqual(exif.get(0x010F), "Canon")  # the useful EXIF is kept
        self.assertEqual(exif.get(0x0112), 1)
        self.assertEqual(exif.get(0x0132), "2026:05:01 10:00:00")
        self.assertNotIn(700, exif)                  # the bulky XMP is not

    def test_a_photo_with_small_exif_keeps_all_of_it(self):
        data = imaging.display_jpeg_bytes(self.tiff_with_exif(100))
        self.assertIn(700, Image.open(io.BytesIO(data)).getexif())


class ColourProfileTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = self.make_tmp()

    def test_thumbnails_and_display_copies_keep_an_rgb_profile(self):
        from PIL import ImageCms
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        path = self.tmp / "p3.tif"
        Image.new("RGB", (80, 60), (10, 200, 30)).save(path, icc_profile=profile)
        thumbs = imaging.image_thumbnails(path, [40, 20])
        for data in thumbs.values():
            self.assertEqual(Image.open(io.BytesIO(data)).info.get("icc_profile"), profile)
        display = Image.open(io.BytesIO(imaging.display_jpeg_bytes(path)))
        self.assertEqual(display.info.get("icc_profile"), profile)

    def test_a_profile_that_is_not_rgb_is_not_put_on_the_rgb_copy(self):
        img = Image.new("CMYK", (8, 8))
        img.info["icc_profile"] = b"\0" * 16 + b"CMYK" + b"\0" * 108
        self.assertIsNone(imaging._rgb_icc_profile(img))


if __name__ == "__main__":
    unittest.main()
