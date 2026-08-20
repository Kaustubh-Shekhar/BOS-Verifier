"""Rendering pages out of a (scanned) BoS PDF."""
import cv2
import numpy as np
import pymupdf

RENDER_DPI = 150


def page_count(path):
    with pymupdf.open(path) as doc:
        return doc.page_count


def render(path, index, dpi=RENDER_DPI):
    """Render one page to a BGR numpy image."""
    with pymupdf.open(path) as doc:
        pix = doc[index].get_pixmap(dpi=dpi)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        if pix.n == 4:
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        if pix.n == 1:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def iter_pages(path, dpi=RENDER_DPI):
    with pymupdf.open(path) as doc:
        for i in range(doc.page_count):
            pix = doc[i].get_pixmap(dpi=dpi)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            elif pix.n == 1:
                img = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
            else:
                img = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            yield i, img
