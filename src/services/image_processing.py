import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from src.config.settings import Settings
from src.schemas.assets import ImageAsset, ImageCreative, Platform

BADGE_VERSION = 'ki-generiert-v2'
OUTPUT_SIZES = {'website': (1536, 1024), 'linkedin': (1200, 1200),
                'google_business_profile': (720, 720)}
EXTENSIONS = {'webp': 'webp', 'jpeg': 'jpg', 'png': 'png'}
MEDIA_TYPES = {'webp': 'image/webp', 'jpeg': 'image/jpeg', 'png': 'image/png'}

@dataclass(frozen=True)
class ProcessedImage:
    data: bytes
    original_format: str
    final_format: str
    original_dimensions: tuple[int, int]
    final_dimensions: tuple[int, int]
    original_file_size: int
    optimized_file_size: int
    size_reduction_percent: float


@dataclass(frozen=True)
class DecodedImage:
    image: Image.Image
    original_format: str
    original_dimensions: tuple[int, int]
    original_file_size: int
    has_alpha: bool

def keyword_slug(value: str) -> str:
    value = value.translate(str.maketrans({'ä':'ae', 'ö':'oe', 'ü':'ue',
                                          'Ä':'ae', 'Ö':'oe', 'Ü':'ue', 'ß':'ss'}))
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii').lower()
    value = re.sub(r'[^a-z0-9]+', '-', value).strip('-')
    return value[:64].rstrip('-')

def public_filename(keyword: str, platform: Platform, version: int, final_format: str) -> str:
    base = keyword_slug(keyword) or 'bild'
    suffix = {'website': '', 'linkedin': '-linkedin',
              'google_business_profile': '-google-business-profile'}[platform]
    version_suffix = f'-v{version}' if version > 1 else ''
    return f'{base}{suffix}{version_suffix}.{EXTENSIONS[final_format]}'

def image_file_path(settings: Settings, asset: ImageAsset) -> Path:
    return settings.output_dir / 'runs' / str(asset.run_id) / 'images' / f'{asset.id}.{EXTENSIONS[asset.final_format]}'

def raw_image_path(settings: Settings, asset: ImageAsset) -> Path:
    return settings.output_dir / 'runs' / str(asset.run_id) / 'image-raw' / f'{asset.id}.raw'


def master_image_path(settings: Settings, master: ImageCreative) -> Path:
    return settings.output_dir / 'runs' / str(master.run_id) / 'image-masters' / f'{master.id}.raw'

def add_badge(image: Image.Image) -> Image.Image:
    image = image.copy()
    width, height = image.size
    font_size = max(10, min(15, round(min(width, height) * 0.014)))
    font = ImageFont.load_default(size=font_size)
    draw = ImageDraw.Draw(image)
    label = 'KI-generiert'
    bounds = draw.textbbox((0, 0), label, font=font)
    text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    padding = max(5, round(font_size * 0.45))
    badge_width = text_width + padding * 3 + font_size
    badge_height = max(text_height + padding * 2, font_size + padding)
    margin = max(7, round(min(width, height) * 0.012))
    left, top = width - badge_width - margin, height - badge_height - margin
    draw.rounded_rectangle((left, top, left + badge_width, top + badge_height),
                           radius=padding, fill='#1B2F4A', outline='white', width=1)
    draw.text((left + padding, top + (badge_height - text_height) / 2 - bounds[1]),
              label, font=font, fill='white')
    sparkle_x = left + badge_width - padding - font_size // 2
    sparkle_y = top + badge_height // 2
    arm = max(2, font_size // 4)
    draw.line((sparkle_x - arm, sparkle_y, sparkle_x + arm, sparkle_y), fill='white', width=1)
    draw.line((sparkle_x, sparkle_y - arm, sparkle_x, sparkle_y + arm), fill='white', width=1)
    return image

def decode_image(raw: bytes) -> DecodedImage:
    if len(raw) > 20_000_000:
        raise ValueError('Provider image exceeds size limit')
    with Image.open(BytesIO(raw)) as source:
        if source.format not in {'PNG', 'JPEG', 'WEBP'}:
            raise ValueError('Unsupported provider image format')
        original_format = source.format.lower()
        original_dimensions = source.size
        if source.width * source.height > 20_000_000:
            raise ValueError('Provider image dimensions exceed limit')
        source = ImageOps.exif_transpose(source)
        has_alpha = 'A' in source.getbands() or 'transparency' in source.info
        if has_alpha:
            source = source.convert('RGBA')
            has_alpha = source.getchannel('A').getextrema()[0] < 255
        source = source.convert('RGBA' if has_alpha else 'RGB')
        return DecodedImage(source, original_format, original_dimensions, len(raw), has_alpha)


def process_image(raw: bytes | DecodedImage, platform: Platform, png_requested: bool,
                  settings: Settings, allow_upscale: bool = True) -> ProcessedImage:
    decoded = decode_image(raw) if isinstance(raw, bytes) else raw
    source = decoded.image
    target = OUTPUT_SIZES[platform]
    if not allow_upscale and (source.width < target[0] or source.height < target[1]):
        raise ValueError('Master image is too small for the platform variant')
    image = source if source.size == target else ImageOps.fit(source, target, method=Image.Resampling.LANCZOS)
    image = add_badge(image)
    final_format = 'webp' if platform != 'google_business_profile' else 'png' if png_requested or decoded.has_alpha else 'jpeg'
    if final_format == 'jpeg':
        image = image.convert('RGB')
    output = BytesIO()
    if final_format == 'webp':
        image.save(output, format='WEBP', quality=settings.image_webp_quality, method=6)
    elif final_format == 'jpeg':
        image.save(output, format='JPEG', quality=settings.image_jpeg_quality,
                   optimize=True, progressive=True, subsampling=0)
    else:
        image.save(output, format='PNG', optimize=True, compress_level=9)
    data = output.getvalue()
    if len(data) > 5_000_000:
        raise ValueError('Optimized image exceeds platform size limit')
    return ProcessedImage(data, decoded.original_format, final_format, decoded.original_dimensions, target,
                          decoded.original_file_size, len(data),
                          round((1 - len(data) / decoded.original_file_size) * 100, 2))
