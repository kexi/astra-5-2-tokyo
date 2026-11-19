// The caller aligns an elevation to face north before rendering one view.
#include "review-building-raster.cpp"

extern "C" void render_facade(int count, const float *xyz, const float *uv, const int *slots,
                              const Paint *paints, int side, uint8_t *rgb, uint32_t *face_ids) {
    render_view(count, xyz, uv, slots, paints, side, Point{0, 1, 0}, Point{-1, 0, 0}, rgb,
                face_ids);
}
