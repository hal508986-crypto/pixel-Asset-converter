from PIL import Image

from pixel_tile_compiler.grammar.clusters import cleanup_pixel_clusters


def test_cluster_cleanup_removes_an_isolated_pixel_but_preserves_large_shapes():
    image = Image.new("RGBA", (5, 5), (20, 20, 20, 255))
    image.putpixel((0, 0), (220, 220, 220, 255))
    for y in range(2, 5):
        for x in range(2, 5):
            image.putpixel((x, y), (220, 220, 220, 255))

    result, metrics = cleanup_pixel_clusters(image, min_cluster_size=2)

    assert result.getpixel((0, 0)) == (20, 20, 20, 255)
    assert result.getpixel((3, 3)) == (220, 220, 220, 255)
    assert metrics.isolated_pixel_count == 1
