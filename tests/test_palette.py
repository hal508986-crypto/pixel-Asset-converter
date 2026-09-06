from PIL import Image

from pixel_tile_compiler.pixelizer.palette import quantize_palette


def test_quantize_palette_is_deterministic_and_respects_budget():
    image = Image.new("RGBA", (8, 8))
    pixels = []
    for y in range(8):
        for x in range(8):
            pixels.append(((x * 31) % 256, (y * 37) % 256, (x * y * 17) % 256, 255))
    image.putdata(pixels)

    first = quantize_palette(image, budget=4, seed=42)
    second = quantize_palette(image, budget=4, seed=42)

    assert first.tobytes() == second.tobytes()
    assert len({pixel for pixel in first.getdata() if pixel[3] != 0}) <= 4


def test_quantize_palette_keeps_alpha_binary():
    image = Image.new("RGBA", (2, 1), (20, 40, 60, 255))
    image.putpixel((0, 0), (20, 40, 60, 0))

    result = quantize_palette(image, budget=4, seed=42)

    assert {result.getpixel((x, 0))[3] for x in range(2)} == {0, 255}


def test_quantize_palette_ignores_rgb_values_in_transparent_pixels():
    def make_image(transparent_rgb: tuple[int, int, int]) -> Image.Image:
        image = Image.new("RGBA", (64, 64), (*transparent_rgb, 0))
        for x in range(16):
            color = ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 0, 255))[x // 4]
            for y in range(16):
                image.putpixel((24 + x, 24 + y), color)
        return image

    first = quantize_palette(make_image((0, 0, 0)), budget=4)
    second = quantize_palette(make_image((17, 231, 93)), budget=4)

    assert first.tobytes() == second.tobytes()


def test_quantize_palette_uses_only_binary_visible_pixels_for_budget():
    image = Image.new("RGBA", (4, 1), (20, 40, 60, 0))
    image.putpixel((0, 0), (255, 0, 0, 127))
    image.putpixel((1, 0), (0, 255, 0, 128))
    image.putpixel((2, 0), (0, 0, 255, 255))

    result = quantize_palette(image, budget=2)

    assert [result.getpixel((x, 0))[3] for x in range(4)] == [0, 255, 255, 0]
    assert len({result.getpixel((x, 0))[:3] for x in range(4) if result.getpixel((x, 0))[3]}) <= 2
