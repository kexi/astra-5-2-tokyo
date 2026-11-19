#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>
#include <vector>
#include <zlib.h>

namespace {

using Color = std::array<uint8_t, 3>;
using Point = std::array<float, 3>;
constexpr int views = 9;
constexpr float pi = 3.14159265358979323846f;
constexpr uint32_t background = UINT32_MAX;

struct Paint {
    const uint8_t *pixels;
    int width;
    int height;
    int repeat;
    Color plain;
};

struct Screen {
    float x, y, depth;
};

float dot(const float *a, const Point &b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

template <typename Pixel> void triangle(const Screen *p, int width, int height, Pixel pixel) {
    const float ax = p[1].x - p[0].x;
    const float ay = p[1].y - p[0].y;
    const float bx = p[2].x - p[0].x;
    const float by = p[2].y - p[0].y;
    const float determinant = ax * by - ay * bx;
    if (std::abs(determinant) < 1e-7f)
        return;

    const float inverse = 1.0f / determinant;
    const auto [lo_x, hi_x] = std::minmax({p[0].x, p[1].x, p[2].x});
    const auto [lo_y, hi_y] = std::minmax({p[0].y, p[1].y, p[2].y});
    const int x0 = std::max(0, static_cast<int>(std::ceil(lo_x - 0.5f)));
    const int y0 = std::max(0, static_cast<int>(std::ceil(lo_y - 0.5f)));
    const int x1 = std::min(width - 1, static_cast<int>(hi_x - 0.5f));
    const int y1 = std::min(height - 1, static_cast<int>(hi_y - 0.5f));
    for (int y = y0; y <= y1; ++y) {
        const float dy = y + 0.5f - p[0].y;
        for (int x = x0; x <= x1; ++x) {
            const float dx = x + 0.5f - p[0].x;
            const float b = (dx * by - dy * bx) * inverse;
            const float c = (dy * ax - dx * ay) * inverse;
            const float a = 1.0f - b - c;
            if (a >= -1e-5f && b >= -1e-5f && c >= -1e-5f)
                pixel(y * width + x, a, b, c);
        }
    }
}

int texel(int index, int size, bool repeat) {
    return repeat ? (index % size + size) % size : std::clamp(index, 0, size - 1);
}

Color sample(const Paint &paint, float u, float v) {
    if (!paint.pixels)
        return paint.plain;

    const auto normalized = [repeat = paint.repeat](float value) {
        return repeat ? value - std::floor(value) : std::clamp(value, 0.0f, 1.0f);
    };
    const float x = normalized(u) * paint.width - 0.5f;
    const float y = normalized(v) * paint.height - 0.5f;
    const int ix = static_cast<int>(std::floor(x));
    const int iy = static_cast<int>(std::floor(y));
    const float fx = x - ix;
    const float fy = y - iy;
    const int x0 = texel(ix, paint.width, paint.repeat);
    const int x1 = texel(ix + 1, paint.width, paint.repeat);
    const int y0 = texel(iy, paint.height, paint.repeat) * paint.width;
    const int y1 = texel(iy + 1, paint.height, paint.repeat) * paint.width;
    Color color{};
    for (int channel = 0; channel < 3; ++channel) {
        const auto pixel = [&](int offset) { return paint.pixels[3 * offset + channel]; };
        const float low = pixel(y0 + x0) * (1 - fx) + pixel(y0 + x1) * fx;
        const float high = pixel(y1 + x0) * (1 - fx) + pixel(y1 + x1) * fx;
        color[channel] = static_cast<uint8_t>(std::lround(low * (1 - fy) + high * fy));
    }
    return color;
}

void render_view(int count, const float *world, const float *uv, const int *slots,
                 const Paint *paints, int side, Point eye, Point right, uint8_t *rgb,
                 uint32_t *face_ids, int margin_scale = 1) {
    const Point up{eye[1] * right[2] - eye[2] * right[1], eye[2] * right[0] - eye[0] * right[2],
                   eye[0] * right[1] - eye[1] * right[0]};
    std::vector<Screen> projected(3 * count);
    float left = std::numeric_limits<float>::infinity(), top = left;
    float right_edge = -left, bottom = -left;
    for (size_t i = 0; i < projected.size(); ++i) {
        const float *point = world + i * 3;
        Screen &p = projected[i];
        p = {dot(point, right), -dot(point, up), dot(point, eye)};
        left = std::min(left, p.x);
        top = std::min(top, p.y);
        right_edge = std::max(right_edge, p.x);
        bottom = std::max(bottom, p.y);
    }
    const float scale =
        (side - 8.0f * margin_scale) / std::max({right_edge - left, bottom - top, 0.001f});
    for (auto &p : projected) {
        p.x = (p.x - 0.5f * (left + right_edge)) * scale + 0.5f * side;
        p.y = (p.y - 0.5f * (top + bottom)) * scale + 0.5f * side;
    }
    std::vector<float> depth(side * side, -std::numeric_limits<float>::infinity());
    std::fill(face_ids, face_ids + side * side, background);
    const Color canvas{27, 32, 37};
    for (int i = 0; i < side * side; ++i)
        std::copy(canvas.begin(), canvas.end(), rgb + i * 3);
    for (int face = 0; face < count; ++face) {
        const Screen *p = projected.data() + face * 3;
        const float *coords = uv + face * 6;
        const Paint &paint = paints[slots[face]];
        triangle(p, side, side, [&](int pixel, float a, float b, float c) {
            const float z = a * p[0].depth + b * p[1].depth + c * p[2].depth;
            if (z <= depth[pixel])
                return;
            depth[pixel] = z;
            face_ids[pixel] = face;
            const Color color = sample(paint, a * coords[0] + b * coords[2] + c * coords[4],
                                       a * coords[1] + b * coords[3] + c * coords[5]);
            std::copy(color.begin(), color.end(), rgb + pixel * 3);
        });
    }
}

void resolve_view(int side, int factor, const uint8_t *samples, const uint32_t *owners,
                  uint8_t *rgb, uint32_t *face_ids) {
    const int sample_side = side * factor;
    const unsigned area = factor * factor;
    for (int y = 0; y < side; ++y) {
        for (int x = 0; x < side; ++x) {
            const int first = y * factor * sample_side + x * factor;
            auto owner = owners[first + (factor / 2) * (sample_side + 1)];
            std::array<unsigned, 3> sum{};
            for (int sy = 0; sy < factor; ++sy) {
                for (int sx = 0; sx < factor; ++sx) {
                    const int sample_index = first + sy * sample_side + sx;
                    if (owner == background)
                        owner = owners[sample_index];
                    for (int channel = 0; channel < 3; ++channel)
                        sum[channel] += samples[sample_index * 3 + channel];
                }
            }
            const int pixel = y * side + x;
            face_ids[pixel] = owner;
            for (int channel = 0; channel < 3; ++channel)
                rgb[pixel * 3 + channel] = (sum[channel] + area / 2) / area;
        }
    }
}

void big_endian(std::vector<uint8_t> &target, uint32_t value) {
    for (int shift = 24; shift >= 0; shift -= 8)
        target.push_back(static_cast<uint8_t>(value >> shift));
}

void png_chunk(std::vector<uint8_t> &output, const char *name, const uint8_t *bytes, size_t size) {
    big_endian(output, static_cast<uint32_t>(size));
    const auto start = output.size();
    output.insert(output.end(), name, name + 4);
    if (size)
        output.insert(output.end(), bytes, bytes + size);
    big_endian(output, static_cast<uint32_t>(crc32(0, output.data() + start, size + 4)));
}

} // namespace

extern "C" void render_building(int count, const float *xyz, const float *uv, const int *slots,
                                const Paint *paints, int side, int factor, uint8_t *rgb,
                                uint32_t *face_ids) {
    const int sample_side = side * factor;
    const auto sample_count = factor > 1 ? sample_side * sample_side : 0;
    std::vector<uint8_t> samples(sample_count * 3);
    std::vector<uint32_t> sample_owners(sample_count);
    for (int view = 0; view < views; ++view) {
        // Azimuth zero faces the north facade. The last view reads the roof at 84°.
        const float azimuth = view == 8 ? pi / 4.0f : view * pi / 4.0f;
        const float elevation = view == 8 ? 84.0f * pi / 180.0f : 14.0f * pi / 180.0f;
        const float sine = std::sin(azimuth), cosine = std::cos(azimuth);
        const Point right{-cosine, sine, 0};
        const Point eye{sine * std::cos(elevation), cosine * std::cos(elevation),
                        std::sin(elevation)};
        auto *frame = rgb + view * side * side * 3;
        auto *frame_owners = face_ids + view * side * side;
        if (factor == 1) {
            render_view(count, xyz, uv, slots, paints, side, eye, right, frame, frame_owners);
        } else {
            render_view(count, xyz, uv, slots, paints, sample_side, eye, right, samples.data(),
                        sample_owners.data(), factor);
            resolve_view(side, factor, samples.data(), sample_owners.data(), frame, frame_owners);
        }
    }
}

extern "C" void atlas_usage(int count, const float *uv, const uint8_t *regions, int width,
                            int height, uint8_t *coverage) {
    for (int face = 0; face < count; ++face) {
        if (!regions[face])
            continue;
        const float *coords = uv + face * 6;
        Screen points[3];
        for (int corner = 0; corner < 3; ++corner) {
            points[corner] = {std::clamp(coords[2 * corner], 0.0f, 1.0f) * width,
                              std::clamp(coords[2 * corner + 1], 0.0f, 1.0f) * height, 0};
        }
        triangle(points, width, height,
                 [&](int pixel, float, float, float) { coverage[pixel] |= regions[face]; });
    }
}

extern "C" int save_review_png(const char *path, int width, int height, const uint8_t *rgb) {
    const size_t stride = 1 + width * 3;
    std::vector<uint8_t> filtered(stride * height);
    for (int row = 0; row < height; ++row) {
        // PNG's Sub filter reduces the repeated background and the flat parts of walls.
        uint8_t *output = filtered.data() + row * stride;
        const uint8_t *source = rgb + row * (stride - 1);
        output[0] = 1;
        std::memcpy(output + 1, source, 3);
        for (size_t i = 3; i < stride - 1; ++i)
            output[i + 1] = source[i] - source[i - 3];
    }
    uLongf compressed_size = compressBound(filtered.size());
    std::vector<uint8_t> compressed(compressed_size);
    if (compress2(compressed.data(), &compressed_size, filtered.data(), filtered.size(), 1) != Z_OK)
        return 0;
    std::vector<uint8_t> output{137, 80, 78, 71, 13, 10, 26, 10};
    std::vector<uint8_t> header;
    big_endian(header, width);
    big_endian(header, height);
    header.insert(header.end(), {8, 2, 0, 0, 0});
    png_chunk(output, "IHDR", header.data(), header.size());
    png_chunk(output, "IDAT", compressed.data(), compressed_size);
    png_chunk(output, "IEND", nullptr, 0);
    std::FILE *file = std::fopen(path, "wb");
    if (!file)
        return 0;
    const bool written = std::fwrite(output.data(), 1, output.size(), file) == output.size();
    return std::fclose(file) == 0 && written;
}
