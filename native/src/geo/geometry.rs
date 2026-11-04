use std::f64::consts::PI;

const MERCATOR_MAX_LAT: f64 = 85.0511287798066;

#[inline]
fn project(longitude: f64, latitude: f64) -> (f64, f64) {
    let latitude = latitude.clamp(-MERCATOR_MAX_LAT, MERCATOR_MAX_LAT);
    let radians = latitude.to_radians();
    let x = (longitude + 180.0) / 360.0;
    let y = (1.0 - (radians.tan().asinh() / PI)) / 2.0;
    (x, y)
}

#[inline]
fn segment_distance_squared(point: (f64, f64), start: (f64, f64), end: (f64, f64)) -> f64 {
    let dx = end.0 - start.0;
    let dy = end.1 - start.1;
    if dx == 0.0 && dy == 0.0 {
        let px = point.0 - start.0;
        let py = point.1 - start.1;
        return px * px + py * py;
    }

    let projection = (((point.0 - start.0) * dx + (point.1 - start.1) * dy) / (dx * dx + dy * dy))
        .clamp(0.0, 1.0);
    let nearest_x = start.0 + projection * dx;
    let nearest_y = start.1 + projection * dy;
    let px = point.0 - nearest_x;
    let py = point.1 - nearest_y;
    px * px + py * py
}

fn douglas_peucker(points: &[(f64, f64)], tolerance: f64) -> Vec<(f64, f64)> {
    if points.len() <= 2 || tolerance <= 0.0 {
        return points.to_vec();
    }

    let threshold = tolerance * tolerance;
    let mut keep = vec![0_u8; points.len()];
    keep[0] = 1;
    keep[points.len() - 1] = 1;

    // Iterative implementation avoids recursion on long, irregular coastlines.
    let mut pending = Vec::with_capacity(32);
    pending.push((0_usize, points.len() - 1));

    while let Some((start_index, end_index)) = pending.pop() {
        let start = points[start_index];
        let end = points[end_index];
        let mut farthest_index = None;
        let mut farthest_distance = threshold;

        for (index, point) in points
            .iter()
            .enumerate()
            .take(end_index)
            .skip(start_index + 1)
        {
            let distance = segment_distance_squared(*point, start, end);
            if distance > farthest_distance {
                farthest_distance = distance;
                farthest_index = Some(index);
            }
        }

        if let Some(index) = farthest_index {
            keep[index] = 1;
            pending.push((start_index, index));
            pending.push((index, end_index));
        }
    }

    points
        .iter()
        .zip(keep.iter())
        .filter_map(|(point, keep)| (*keep != 0).then_some(*point))
        .collect()
}

/// Project a geographic ring to Web Mercator and simplify it in one native pass.
///
/// The thresholds intentionally match the Python implementation: a 25% radial
/// pre-filter followed by a 75% Douglas-Peucker tolerance budget.
pub(crate) fn project_and_simplify_ring(points: &[(f64, f64)], tolerance: f64) -> Vec<(f64, f64)> {
    if points.len() <= 2 || tolerance <= 0.0 {
        return points
            .iter()
            .map(|(longitude, latitude)| project(*longitude, *latitude))
            .collect();
    }

    let radial_threshold = (tolerance * 0.25).powi(2);
    let first = project(points[0].0, points[0].1);
    let mut radial = Vec::with_capacity(points.len().min(4096));
    radial.push(first);
    let mut last = first;

    for &(longitude, latitude) in points.iter().take(points.len() - 1).skip(1) {
        let projected = project(longitude, latitude);
        let dx = projected.0 - last.0;
        let dy = projected.1 - last.1;
        if dx * dx + dy * dy >= radial_threshold {
            radial.push(projected);
            last = projected;
        }
    }

    let final_point = project(points[points.len() - 1].0, points[points.len() - 1].1);
    if final_point != *radial.last().expect("the first point is always present") {
        radial.push(final_point);
    }

    if radial.len() <= 2 {
        return radial;
    }

    douglas_peucker(&radial, tolerance * 0.75)
}

#[cfg(test)]
mod tests {
    use super::{project, project_and_simplify_ring, segment_distance_squared};

    #[test]
    fn projection_clamps_latitude_to_web_mercator_limit() {
        let north = project(0.0, 90.0);
        let south = project(0.0, -90.0);
        assert!((north.1 - 0.0).abs() < 1e-12);
        assert!((south.1 - 1.0).abs() < 1e-12);
        assert!((north.0 - 0.5).abs() < 1e-12);
    }

    #[test]
    fn radial_and_douglas_peucker_simplification_keep_endpoints() {
        let line = [(0.0, 0.0), (0.2, 0.000_001), (0.4, -0.000_001), (0.6, 0.0)];
        let simplified = project_and_simplify_ring(&line, 0.001);
        assert_eq!(simplified.len(), 2);
        assert_eq!(simplified.first(), Some(&project(0.0, 0.0)));
        assert_eq!(simplified.last(), Some(&project(0.6, 0.0)));
    }

    #[test]
    fn simplification_preserves_a_clear_corner() {
        let curve = [(0.0, 0.0), (0.5, 0.2), (1.0, 0.0)];
        assert_eq!(project_and_simplify_ring(&curve, 0.000_001).len(), 3);
    }

    #[test]
    fn two_point_and_zero_tolerance_rings_are_projected_without_simplification() {
        let line = [(0.0, 0.0), (10.0, 10.0), (20.0, 0.0)];
        assert_eq!(project_and_simplify_ring(&line, 0.0).len(), 3);
        assert_eq!(project_and_simplify_ring(&line[..2], 0.1).len(), 2);
    }

    #[test]
    fn zero_length_segments_have_a_finite_distance() {
        assert_eq!(
            segment_distance_squared((1.0, 1.0), (0.0, 0.0), (0.0, 0.0)),
            2.0
        );
    }
}
