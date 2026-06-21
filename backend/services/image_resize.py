"""Image downscale + EXIF strip on upload.

Wired into the flyer upload (routes/flyers.py:upload_flyer_image) and
the artist/venue media upload path (routes/media.py). Goal: cap the
on-disk image at a sensible max dimension so mobile pages don't pull a
10 MB hero photo just to display it at 800×600.

Behavior:
  - Inputs that decode as raster images (PNG / JPG / GIF / WEBP) are
    re-encoded at <= MAX_DIM on the long edge, keeping aspect ratio.
  - EXIF / XMP / IPTC metadata is dropped on re-encode (privacy: phones
    embed GPS coords by default).
  - Animated GIFs and animated WEBPs are passed through unchanged —
    re-encoding loses the animation, and the bandwidth wins from a
    static fallback aren't worth the broken UX.
  - If decode fails (e.g. PIL can't parse the bytes — already validated
    via magic-byte check upstream so this is rare), the original bytes
    are returned unchanged. Better to ship the raw upload than fail
    the request.
  - JPEG is re-encoded at quality 85 (default), PNG/WEBP/GIF kept in
    their native format so transparency / animation aren't lost.

Returns the (possibly resized) bytes — caller writes them to disk.
"""
import io
import logging

logger = logging.getLogger("gigsfill.image_resize")

# 2400px is a sweet spot — fits Retina @ 2x on a 1200px viewport, halves
# the bandwidth of a 4000px DSLR upload, doesn't degrade quality at the
# sizes we actually display (flyer modal max ~1200, profile hero ~800).
MAX_DIM = 2400
JPEG_QUALITY = 85


def resize_if_needed(content: bytes, ext: str) -> bytes:
    """Decode -> resize -> re-encode the image. Returns new bytes
    (which may equal `content` if no resize was needed).

    `ext` is the lowercase extension without the leading dot
    (e.g. "png", "jpg", "webp", "gif"). The flyer/media upload paths
    have already extension-allowlisted and magic-byte-verified the
    bytes before this is called, so we trust both.
    """
    try:
        from PIL import Image, ImageOps
    except Exception as e:
        logger.warning(f"Pillow not installed — skipping resize ({e})")
        return content

    try:
        img = Image.open(io.BytesIO(content))
        # Animated formats: pass through. is_animated is missing on
        # static images, so the getattr fallback is the safe check.
        if getattr(img, "is_animated", False):
            return content

        # Honor EXIF orientation BEFORE measuring dimensions so the
        # resize compares the visually-correct edge length.
        img = ImageOps.exif_transpose(img)

        w, h = img.size
        long_edge = max(w, h)
        if long_edge <= MAX_DIM:
            # Already small enough — but we still re-encode to strip
            # EXIF (privacy) when the format is JPEG. PNG/WEBP/GIF
            # don't typically carry EXIF; pass them through unchanged
            # to avoid the lossy re-encode cost.
            if ext in ("jpg", "jpeg"):
                buf = io.BytesIO()
                # Convert to RGB if mode is RGBA/P — JPEG can't carry
                # an alpha channel, and PIL would raise without conv.
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
                return buf.getvalue()
            return content

        # Resize. LANCZOS is the highest-quality downscale filter
        # PIL ships; cost is negligible at our sizes (one-time on
        # upload, not per-render).
        ratio = MAX_DIM / float(long_edge)
        new_w = int(round(w * ratio))
        new_h = int(round(h * ratio))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        if ext in ("jpg", "jpeg"):
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        elif ext == "png":
            img.save(buf, format="PNG", optimize=True)
        elif ext == "webp":
            img.save(buf, format="WEBP", quality=JPEG_QUALITY, method=6)
        elif ext == "gif":
            img.save(buf, format="GIF", optimize=True)
        else:
            # Unknown — caller already allowlisted, but be defensive.
            return content
        out = buf.getvalue()
        logger.info(
            f"[IMG_RESIZE] {ext} {w}x{h} ({len(content):,}B) -> "
            f"{new_w}x{new_h} ({len(out):,}B) "
            f"({100*len(out)//max(1,len(content))}% of original)"
        )
        return out
    except Exception as e:
        # Whatever went wrong, ship the original bytes — never fail a
        # legitimate upload over a resize hiccup.
        logger.warning(f"[IMG_RESIZE] failed for ext={ext}: {e} — passing through original")
        return content
