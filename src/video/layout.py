"""Geometry checks shared by preview and final renders."""
from dataclasses import dataclass

MIN_FONT_SIZE = 28

class VisualQualityError(ValueError):
    pass

@dataclass(frozen=True)
class LayoutBox:
    name: str
    left: float
    right: float
    bottom: float
    top: float
    font_size: float | None = None


def validate_contrast(foreground, background=(0, 0, 0), *, opacity: float = 1.0) -> float:
    """WCAG-style luminance check for explanatory text, including its opacity."""
    def luminance(rgb):
        values = [c / 12.92 if c <= 0.04045 else ((c+0.055)/1.055)**2.4 for c in rgb]
        return sum(c*w for c, w in zip(values, (0.2126, 0.7152, 0.0722)))
    mixed = [a*opacity+b*(1-opacity) for a, b in zip(foreground, background)]
    a, b = sorted((luminance(mixed), luminance(background)))
    ratio = (b+0.05)/(a+0.05)
    if ratio < 4.5 or opacity < 0.8:
        raise VisualQualityError('Explanatory text is too dim or has insufficient contrast; keep text bright and readable.')
    return ratio

def validate_layout(boxes: list[LayoutBox], *, width: float = 14.222,
                    height: float = 8, margin: float = 0.25) -> None:
    for box in boxes:
        if box.left < -width/2 + margin or box.right > width/2 - margin or box.bottom < -height/2 + margin or box.top > height/2 - margin:
            raise VisualQualityError(f"{box.name} exceeds safe frame boundaries. Split or reflow this content.")
        if box.font_size is not None and box.font_size < MIN_FONT_SIZE - 0.1:
            raise VisualQualityError(f"{box.name} uses unreadable {box.font_size:.1f}pt text; minimum is {MIN_FONT_SIZE}pt.")
    for index, first in enumerate(boxes):
        for second in boxes[index+1:]:
            overlap_x = min(first.right, second.right) - max(first.left, second.left)
            overlap_y = min(first.top, second.top) - max(first.bottom, second.bottom)
            if overlap_x > 0.04 and overlap_y > 0.04:
                raise VisualQualityError(f"Layout overlap: {first.name} and {second.name}. Reflow these regions.")
