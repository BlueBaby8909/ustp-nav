from flask import Flask, render_template, request, jsonify
import osmnx as ox
import networkx as nx
import folium
import geopandas as gpd

app = Flask(__name__)

# --- GLOBAL SETUP ---
print("Initializing Map Data... (This takes a few seconds)")
PLACE_NAME = "University of Science and Technology of Southern Philippines"

# 1. Load the Graph (The walking network)
G = ox.graph_from_place(PLACE_NAME, network_type="walk")

# 2. Load Buildings (For dropdowns & destinations)
buildings_gdf = ox.features_from_place(PLACE_NAME, tags={"building": True})
available_buildings = sorted(buildings_gdf['name'].dropna().unique().tolist())

# 3. CREATE CAMPUS BOUNDARY (Visuals & Camera Lock)
try:
    # Attempt to fetch official boundary
    campus_area_gdf = ox.geocode_to_gdf(PLACE_NAME)
    geom_type = campus_area_gdf.geometry.iloc[0].geom_type

    if "Polygon" in geom_type:
        campus_area = campus_area_gdf.geometry
    else:
        print("Notice: OSM only has a point. Creating circular boundary.")
        campus_area = campus_area_gdf.to_crs(epsg=3857).buffer(300).to_crs(epsg=4326)

except Exception as e:
    print(f"Boundary lookup failed ({e}). Using building perimeter.")
    campus_polygon = buildings_gdf.union_all().convex_hull
    campus_area = gpd.GeoSeries([campus_polygon], crs="EPSG:4326").buffer(0.0005)

# 4. Calculate Map Center & Limits
min_lon, min_lat, max_lon, max_lat = campus_area.total_bounds
center_lat = (min_lat + max_lat) / 2
center_lon = (min_lon + max_lon) / 2


# --- HELPER: GENERATE MAP ---
def create_map(route=None, start_point=None, end_point=None, start_name=None, end_name=None, route_coords=None):
    # Initialize Map
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=18,
        tiles='cartodbdark_matter',
        min_zoom=17, max_zoom=20, max_bounds=True,
        min_lat=min_lat - 0.002, max_lat=max_lat + 0.002,
        min_lon=min_lon - 0.002, max_lon=max_lon + 0.002
    )

    # Add Campus Highlight Layer
    folium.GeoJson(
        campus_area,
        style_function=lambda x: {
            'fillColor': '#1e1e1e',
            'color': '#00e676',  # Neon Green Border
            'weight': 2,
            'dashArray': '5, 5',
            'fillOpacity': 0.3
        }
    ).add_to(m)

    # Add Interactive Buildings Layer
    named_buildings = buildings_gdf[buildings_gdf['name'].notna()]
    folium.GeoJson(
        named_buildings,
        name="Buildings",
        style_function=lambda x: {
            'fillColor': '#C0C0C0',  # Silver
            'color': '#808080',  # Grey Border
            'weight': 1,
            'fillOpacity': 0.6
        },
        highlight_function=lambda x: {
            'fillColor': '#ffffff',  # White on hover
            'weight': 2,
            'fillOpacity': 0.9
        },
        tooltip=folium.GeoJsonTooltip(
            fields=['name'],
            aliases=['Building:'],
            style=("background-color: black; color: white; font-family: arial; font-weight: bold; padding: 5px;")
        )
    ).add_to(m)

    # Draw Route (if provided)
    if route and route_coords:
        folium.PolyLine(route_coords, color="#00e676", weight=5, opacity=0.9).add_to(m)

        folium.Marker(
            [start_point.y, start_point.x], popup=f"Start: {start_name}",
            icon=folium.Icon(color="green", icon="play", prefix='fa')
        ).add_to(m)

        folium.Marker(
            [end_point.y, end_point.x], popup=f"End: {end_name}",
            icon=folium.Icon(color="red", icon="stop", prefix='fa')
        ).add_to(m)

    return m


print("Server Ready!")


# --- ROUTES ---
@app.route('/')
def index():
    m = create_map()
    return render_template('index.html',
                           buildings=available_buildings,
                           initial_map=m._repr_html_())


@app.route('/navigate', methods=['POST'])
def navigate():
    data = request.get_json()
    start_name = data.get('start_point')
    end_name = data.get('end_point')

    try:
        if start_name == end_name:
            return jsonify({'error': "Start and Destination cannot be the same."}), 400

        # 1. Find Building Locations
        origin_row = buildings_gdf[buildings_gdf["name"] == start_name].iloc[0]
        dest_row = buildings_gdf[buildings_gdf["name"] == end_name].iloc[0]

        orig_point = origin_row.geometry.centroid
        dest_point = dest_row.geometry.centroid

        # 2. Find Nearest Nodes
        orig_node = ox.nearest_nodes(G, orig_point.x, orig_point.y)
        dest_node = ox.nearest_nodes(G, dest_point.x, dest_point.y)

        # 3. Calculate Path (The Nodes)
        route = nx.shortest_path(G, orig_node, dest_node, weight='length')

        # 4. EXTRACT PRECISE GEOMETRY (The Curves)
        route_coords = []

        # Add the starting node explicitly first
        start_node_y = G.nodes[route[0]]['y']
        start_node_x = G.nodes[route[0]]['x']
        route_coords.append((start_node_y, start_node_x))

        # Loop through edges to find geometry
        for u, v in zip(route[:-1], route[1:]):
            edge_data = G.get_edge_data(u, v)[0]

            if 'geometry' in edge_data:
                geo_coords = [(lat, lon) for lon, lat in edge_data['geometry'].coords]
                route_coords.extend(geo_coords)
            else:
                # If straight line, just add the next node's coordinates
                node_y = G.nodes[v]['y']
                node_x = G.nodes[v]['x']
                route_coords.append((node_y, node_x))

        # 5. Generate Map
        m = create_map(route, orig_point, dest_point, start_name, end_name, route_coords)

        return jsonify({'map_html': m._repr_html_()})

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)