"""Complete 3x3 rack topology; dimensions/placement must be supplied explicitly."""
from itertools import product

from simulation_safety.cad_geometry import add, apply, rpy
from simulation_safety.supervisor import SafetyError, finite


def rack_boxes(*, origin, orientation, height, pitch, grid_bottom, depth,
               divider_thickness, tray_thickness, rim_height, rim_thickness,
               uncertainty):
    dimensions = (height, pitch, depth, divider_thickness, tray_thickness,
                  rim_height, rim_thickness)
    if (not isinstance(origin, (tuple, list)) or not isinstance(orientation, (tuple, list))
            or any(not finite(v) or v <= 0 for v in dimensions)
            or not finite(grid_bottom) or grid_bottom < 0
            or grid_bottom+3*pitch > height
            or not finite(uncertainty) or uncertainty < 0
            or divider_thickness >= pitch or 2*rim_thickness >= min(pitch, depth)
            or rim_height >= pitch or tray_thickness >= pitch
            or len(origin) != 3 or len(orientation) != 3
            or not all(finite(v) for v in (*origin, *orientation))):
        raise SafetyError('complete rack dimensions, pose and uncertainty required')
    transform = rpy(orientation)
    boxes = {}
    def box(name, lo, hi):
        corners = [add(origin, apply(transform, p)) for p in product(*zip(lo, hi))]
        boxes[name] = (tuple(min(p[j] for p in corners)-uncertainty for j in range(3)),
                       tuple(max(p[j] for p in corners)+uncertainty for j in range(3)))
    t = divider_thickness
    for col in range(4):
        x = col*pitch
        box(f'frame_vertical_{col}', (x-t/2, 0, 0), (x+t/2, depth, height))
    levels = sorted(set([0., height]+[grid_bottom+i*pitch for i in range(4)]))
    for index, z in enumerate(levels):
        box(f'shelf_horizontal_{index}', (-t/2, 0, z-t/2), (3*pitch+t/2, depth, z+t/2))
    for col in range(3):
        for row in range(3):
            x, z = col*pitch, grid_bottom+(2-row)*pitch
            prefix = f'c{col+1}r{row+1}_tray'
            box(prefix+'_floor', (x, 0, z), (x+pitch, depth, z+tray_thickness))
            for name, lo, hi in (
                ('front_rim', (x, 0, z), (x+pitch, rim_thickness, z+rim_height)),
                ('back_rim', (x, depth-rim_thickness, z), (x+pitch, depth, z+rim_height)),
                ('left_rim', (x, 0, z), (x+rim_thickness, depth, z+rim_height)),
                ('right_rim', (x+pitch-rim_thickness, 0, z), (x+pitch, depth, z+rim_height))):
                box(prefix+'_'+name, lo, hi)
    return boxes
