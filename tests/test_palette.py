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
