// Thin wrapper around Leaflet for the Map page. Leaflet itself is loaded from a CDN in App.razor.

let map = null;
let layer = null;
let lastBounds = [];

export function init(element) {
    if (!window.L) return false; // Leaflet didn't load (offline, or the CDN is blocked)

    if (map) map.remove();
    map = L.map(element, { preferCanvas: true }).setView([-41.0, 174.0], 5); // New Zealand
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    layer = L.layerGroup().addTo(map);

    // The container can still be settling into its grid cell on the first frame
    setTimeout(() => map && map.invalidateSize(), 0);
    return true;
}

// points: [{ lat, lng, color, popup }]. popup is HTML the page has already escaped.
export function setMarkers(points, fit) {
    if (!map) return;
    layer.clearLayers();
    lastBounds = [];

    for (const p of points) {
        L.circleMarker([p.lat, p.lng], {
            radius: 8,
            weight: 1.5,
            color: "#1f2d33",
            opacity: 0.9,
            fillColor: p.color,
            fillOpacity: 0.92,
        })
            .bindPopup(p.popup, { maxWidth: 260 })
            .addTo(layer);
        lastBounds.push([p.lat, p.lng]);
    }

    if (fit) fitToMarkers();
}

export function fit() {
    fitToMarkers();
}

function fitToMarkers() {
    if (map && lastBounds.length > 0) {
        map.fitBounds(lastBounds, { padding: [30, 30], maxZoom: 16 });
    }
}

export function dispose() {
    if (map) {
        map.remove();
        map = null;
        layer = null;
        lastBounds = [];
    }
}