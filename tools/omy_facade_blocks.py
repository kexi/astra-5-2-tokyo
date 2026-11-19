"""Same-building facade donors for the Marunouchi and Otemachi blocks."""


def rectangle(left, top, right, bottom):
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


# The eastern gable is the only long run of photographed bays on the Otemachi
# Building. Its 15.3-pixel pier and 16.6-pixel floor spacings are measured on
# the 5 px/m orthographic plate. Stop the sampled rows above the street trees.
BAYS = (54 / 281, 101 / 260, 146 / 281, 234 / 260)
GLASS = (9 / 281, 102 / 260, 53 / 281, 217 / 260)
ENTRANCE = (57 / 281, 235 / 260, 145 / 281, 255 / 260)
PLANT = (141 / 281, 50 / 260, 181 / 281, 83 / 260)


def long_elevation(side):
    # The architect's published north elevation indicates the five finish zones
    # along the 200 m grid. Their source colours and window bays come from this
    # building's PLATEAU east end; material transitions are an interpretation.
    zones = [
        (0, 0.16, BAYS, (1.07, 0.96, 0.90)),
        (0.16, 0.46, BAYS, (0.73, 0.76, 0.78)),
        (0.46, 0.54, GLASS, (1.03, 1.10, 1.15)),
        (0.54, 0.84, BAYS, (0.94, 0.98, 1.01)),
        (0.84, 1, BAYS, (1.06, 1.10, 1.11)),
    ]
    result = []
    for left, right, source, tint in zones:
        if side == 3:
            left, right = 1 - right, 1 - left
        result.append((side, rectangle(left, 0.375, right, 0.918), 0, source, tint))
    result.append((side, rectangle(0, 0.918, 1, 1), 0, ENTRANCE, 0.95))
    # HVAC plinths have painted walls. Keep their parapets, roof surfaces,
    # chimneys and individual roof-mounted units out of the window grid.
    result.append((side, rectangle(0.076, 0.157, 0.922, 0.375), 0, PLANT, 0.91))
    return result


EXTRA_REPAIRS = {
    "丸の内二重橋ビル": (
        -15.36,
        [
            # End at a photographed vertical bay: the rooftop also casts a
            # darker triangular strip just outside its bright parapet.
            (1, rectangle(0, 0.362, 0.372, 0.792), 1, (0.365, 0.239, 0.643, 0.708), 1),
            (1, rectangle(0, 0.792, 0.372, 1), 1, (0.401, 0.8, 0.96, 0.983), 1),
        ],
    ),
    "大手町ビル": (
        -10.95,
        [
            *long_elevation(1),
            *long_elevation(3),
            (2, rectangle(0, 0.378, 1, 0.918), 0, BAYS, (1.06, 1.10, 1.11)),
            (2, rectangle(0, 0.918, 1, 1), 0, ENTRANCE, 0.99),
            (2, rectangle(0.045, 0.197, 0.293, 0.378), 0, PLANT, 0.95),
        ],
    ),
    "丸の内北口ビル": (
        -15.16,
        [
            (
                0,
                [(0.26, 0.695), (0.88, 0.69), (0.81, 1), (0.185, 1)],
                0,
                (0.04, 0.28, 0.975, 0.612),
                1,
            ),
            # The opposite tower projects a helipad and its entire podium onto
            # this elevation. Keep the actual blind gables beside the window bays.
            (
                2,
                [
                    (0, 0.322),
                    (0.535, 0.322),
                    (0.535, 0.306),
                    (0.795, 0.31),
                    (0.794, 0.85),
                    (0.963, 0.85),
                    (0.963, 1),
                    (0, 1),
                ],
                2,
                (0.009, 0.132, 0.98, 0.304),
                1,
            ),
            (
                3,
                [
                    (0, 0.418),
                    (0.31, 0.418),
                    (0.32, 0.443),
                    (0.69, 0.446),
                    (0.69, 0.494),
                    (0.853, 0.494),
                    (0.94, 1),
                    (0, 1),
                ],
                3,
                (0.011, 0.166, 0.98, 0.415),
                1,
            ),
        ],
    ),
    "大手町タワー": (
        -14.08,
        [
            # The photographed upper and mid-height helipads appear along
            # with neighbouring towers across the east face. The centre bay
            # of the original east glass retains its own lighter daylight.
            (0, rectangle(0, 0, 1, 1), 0, (0.207, 0.226, 0.59, 0.454), 1),
            (1, rectangle(0, 0, 1, 1), 3, (0.421, 0.038, 0.825, 0.85), 0.94),
            (
                2,
                [
                    (0, 1),
                    (0.058, 0.832),
                    (0.162, 0.784),
                    (0.587, 0.784),
                    (0.619, 0.795),
                    (0.619, 0.914),
                    (0.706, 0.96),
                    (0.913, 0.96),
                    (0.939, 1),
                ],
                2,
                (0.016, 0.303, 0.981, 0.678),
                1,
            ),
            (
                3,
                [
                    (0, 0.534),
                    (0.205, 0.534),
                    (0.205, 0.557),
                    (0.373, 0.562),
                    (0.373, 1),
                    (0, 1),
                ],
                3,
                (0.009, 0.222, 0.37, 0.527),
                1,
            ),
        ],
    ),
    "丸の内二丁目ビル": (
        -14.87,
        [
            # The source east wall carries an entire equipment roof. A dark
            # screened plant floor and offices remain legible on the west.
            (
                0,
                rectangle(0.075, 0, 0.868, 0.271),
                2,
                (0.235, 0.066, 0.762, 0.232),
                0.90,
            ),
            (
                0,
                rectangle(0.075, 0.271, 0.868, 0.78),
                2,
                (0.025, 0.288, 0.918, 0.564),
                0.93,
            ),
            (
                0,
                rectangle(0.225, 0.78, 0.937, 1),
                0,
                (0.017, 0.79, 0.208, 0.977),
                1,
            ),
        ],
    ),
    "JPタワー": (
        -15.02,
        [
            # The original post office has punched windows at ground level.
            # The tower's broad horizontal bands only resume above its podium.
            (
                3,
                [(0.162, 0.644), (0.516, 0.644), (0.502, 0.841), (0.149, 0.841)],
                3,
                (0.063, 0.27, 0.521, 0.536),
                1,
            ),
            (
                3,
                rectangle(0, 0.839, 0.934, 1),
                1,
                (0.451, 0.843, 0.982, 0.968),
                1.06,
            ),
            (
                2,
                rectangle(0.119, 0.899, 1, 1),
                1,
                (0.451, 0.843, 0.982, 0.968),
                1.02,
            ),
        ],
    ),
    "新丸の内ビルディング": (
        -15.07,
        [
            # The west crane boom spans multiple planes of the stepped
            # tower. Match the actual stepped bays above the crane and the
            # east podium's shopfronts below the true podium line.
            (
                2,
                rectangle(0.184, 0.618, 0.945, 0.844),
                2,
                (0.189, 0.373, 0.952, 0.594),
                1,
            ),
            (2, rectangle(0, 0.844, 0.936, 1), 0, (0.061, 0.855, 0.875, 0.988), 0.76),
            (
                1,
                [
                    (0, 0.643),
                    (0.213, 0.642),
                    (0.222, 0.629),
                    (0.662, 0.629),
                    (0.663, 0.643),
                    (0.842, 0.642),
                    (0.82, 0.846),
                    (0, 0.846),
                ],
                3,
                (0.12, 0.365, 0.971, 0.658),
                0.87,
            ),
            (1, rectangle(0, 0.846, 1, 1), 3, (0.475, 0.856, 0.947, 0.985), 0.95),
            (
                3,
                [(0, 0.843), (0.439, 0.843), (0.411, 1), (0, 1)],
                3,
                (0.475, 0.856, 0.947, 0.985),
                1,
            ),
        ],
    ),
}
