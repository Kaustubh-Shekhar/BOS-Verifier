"""Low-level image helpers shared by the detectors."""
import cv2
import numpy as np


def ink_layers(bgr):
    """Split a scanned page into the cues the detectors care about.

    Returns (chroma, dark) as uint8 maps:
      chroma - how colourful a pixel is; rubber stamps and pen ink are
               chromatic (blue/violet/green) while printed body text and
               table rules are near-neutral.
      dark   - how much darker a pixel is than the *local* paper, which
               keeps faded scans and grey shadows from washing the signal out.
    """
    b, g, r = cv2.split(bgr.astype(np.int16))
    mx = np.maximum(np.maximum(b, g), r)
    mn = np.minimum(np.minimum(b, g), r)
    chroma = np.clip(mx - mn, 0, 255).astype(np.uint8)

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    paper = cv2.medianBlur(gray, 51)
    dark = cv2.subtract(paper, gray)
    return chroma, dark


def paper_tint(bgr, sample=8):
    """Median chroma of the page: high for scans with an overall colour cast."""
    small = bgr[::sample, ::sample]
    b, g, r = cv2.split(small.astype(np.int16))
    mx = np.maximum(np.maximum(b, g), r)
    mn = np.minimum(np.minimum(b, g), r)
    return float(np.median(mx - mn))
