import numpy as np
from vispy import gloo, app, io

from surface import PlaneWaves, CircularWaves
from bed import BedLiner, BedLog, BedCircular

vertex = ("""
#version 120

uniform float u_eye_height;
uniform mat4 u_world_view;
uniform float u_alpha;
uniform float u_bed_depth;

attribute vec2 a_position;
attribute vec2 a_start_position;
attribute float a_height;
attribute float a_bed_depth;
attribute vec2 a_normal;

varying vec3 v_normal;
varying vec3 v_position;
varying vec3 v_reflected;
varying vec2 v_sky_texcoord;
varying vec2 v_bed_texcoord;
varying float v_reflectance;
varying vec3 v_mask;
varying float bed_upper_water;

void main (void) {
    v_normal = normalize(vec3(a_normal, -1));
    v_position = vec3(a_position.xy, a_height);

    float total_depth = -a_bed_depth - u_bed_depth;

    vec4 position_view = u_world_view * vec4(v_position, 1);
    float z = 1 - (1 + position_view.z) / (1 + u_eye_height);
    gl_Position = vec4(position_view.xy, -position_view.z*z, z);

    vec4 depth_position_view = u_world_view * vec4(a_start_position.xy, total_depth, 1);
    float z_depth = 1 - (1 + depth_position_view.z) / (1 + u_eye_height);

    vec4 eye_view = vec4(0, 0, u_eye_height, 1);
    vec4 eye = transpose(u_world_view) * eye_view;
    vec3 from_eye = normalize(v_position - eye.xyz);
    vec3 normal = normalize(-v_normal);
    v_reflected = normalize(from_eye - 2 * normal * dot(normal, from_eye));
    v_sky_texcoord=0.05 * v_reflected.xy / v_reflected.z + vec2(0.5 ,0.5);

    vec3 cr = cross(normal, from_eye);
    float d = 1 - u_alpha * u_alpha * dot(cr, cr);
    float c2 = sqrt(d);
    vec3 refracted = normalize(u_alpha * cross(cr, normal) - normal * c2);

    float c1 =- dot(normal, from_eye);
    float t = (total_depth - v_position.z) / refracted.z;
    vec3 point_on_bed = v_position + t * refracted;
    if (total_depth > a_height) {
        point_on_bed = v_position;
        gl_Position = vec4(depth_position_view.xy, -position_view.z * z_depth, z_depth);
        bed_upper_water = 1;
    } else {
        bed_upper_water = 0;
    }
    v_bed_texcoord = point_on_bed.xy + vec2(0.5, 0.5);

    float reflectance_s = pow((u_alpha * c1 - c2) / (u_alpha * c1 + c2), 2);
    float reflectance_p = pow((u_alpha * c2 - c1) / (u_alpha * c2 + c1), 2);
    v_reflectance = (reflectance_s + reflectance_p) / 2;

    float diw = length(point_on_bed - v_position);
    vec3 filter = vec3(1, 0.5, 0.2);
    v_mask = vec3(exp(-diw * filter.x), exp(-diw * filter.y), exp(-diw * filter.z));
}
""")

fragment_triangle = ("""
#version 120

uniform sampler2D u_sky_texture;
uniform sampler2D u_bed_texture;
uniform vec3 u_sun_direction;
uniform vec3 u_sun_diffused_color;
uniform vec3 u_sun_reflected_color;
uniform float u_reflected_mult;
uniform float u_diffused_mult;
uniform float u_bed_mult;
uniform float u_depth_mult;
uniform float u_sky_mult;

varying vec3 v_normal;
varying vec3 v_position;
varying vec3 v_reflected;
varying vec2 v_sky_texcoord;
varying vec2 v_bed_texcoord;
varying float v_reflectance;
varying vec3 v_mask;
varying float bed_upper_water;

void main (void) {

    vec3 sky_color = texture2D(u_sky_texture, v_sky_texcoord).rgb;
    vec3 bed_color = texture2D(u_bed_texture, v_bed_texcoord).rgb;

    vec3 normal = normalize(v_normal);
    float diffused_intensity = u_diffused_mult * max(0, -dot(normal, u_sun_direction));
    float cosphi = max(0, dot(u_sun_direction, normalize(v_reflected)));
    float reflected_intensity = u_reflected_mult * pow(cosphi, 20);

    vec3 ambient_water = vec3(0, 0.3, 0.5);
    vec3 image_color = u_bed_mult * bed_color * v_mask + u_depth_mult * ambient_water * (1 - v_mask);
    vec3 rgb = u_sky_mult * sky_color * v_reflectance
        + image_color * (1 - v_reflectance)
        + diffused_intensity * u_sun_diffused_color
        + reflected_intensity * u_sun_reflected_color;
    if (bed_upper_water > 0.5) {
        rgb = bed_color;
    }
    gl_FragColor = vec4(rgb, 1.0);
}
""")

fragment_point = """
#version 120

void main() {
    gl_FragColor = vec4(1, 0, 0, 1);
}
"""

def normalize(vec):
    vec = np.asarray(vec, dtype=np.float32)
    return vec / np.sqrt(np.sum(vec * vec, axis=-1))[..., None]

class Canvas(app.Canvas):
    def __init__(self, surface, bed, new_waves_class=None, size=(1000, 1000), sky_img_path="fluffy_clouds.png", bed_img_path="seabed.png"):
        app.Canvas.__init__(self, size=size,
                            title="Water surface simulator 2")
        # запрещаем текст глубины depth_test (все точки будут отрисовываться),
        # запрещает смещивание цветов blend - цвет пикселя на экране равен gl_fragColor.
        gloo.set_state(clear_color=(0, 0, 0, 1), depth_test=True, blend=True)
        self.program = gloo.Program(vertex, fragment_triangle)
        self.program_point = gloo.Program(vertex, fragment_point)

        self.surface = surface
        self.surface_class = new_waves_class
        self.surface_wave_list = []
        self.add_wave_center((self.size[0] / 2, self.size[1] / 2))

        self.bed = bed
        self.sky_img = io.read_png(sky_img_path)
        self.bed_img = io.read_png(bed_img_path)
        # xy координаты точек сразу передаем шейдеру, они не будут изменятся со временем
        self.program["a_position"] = self.surface.position()
        self.program["a_start_position"] = self.surface.position()
        self.program_point["a_position"] = self.surface.position()

        self.program['u_sky_texture'] = gloo.Texture2D(
            self.sky_img, wrapping='repeat', interpolation='linear')
        self.program['u_bed_texture'] = gloo.Texture2D(
            self.bed_img, wrapping='repeat', interpolation='linear')

        self.program_point["u_eye_height"] = self.program["u_eye_height"] = 3

        self.program["u_alpha"] = 0.3
        self.program["a_bed_depth"] = self.bed.depth()
        self.program["u_bed_depth"] = 0.0

        self.program["u_sun_direction"] = normalize([0, 1, 0.1])
        self.program["u_sun_diffused_color"] = [1, 0.8, 1]
        self.program["u_sun_reflected_color"] = [1, 0.8, 0.6]

        self.triangles = gloo.IndexBuffer(self.surface.triangulation())

        # Set up GUI
        self.camera = np.array([0, 0, 1])
        self.up = np.array([0, 1, 0])
        self.set_camera()
        self.are_points_visible = False
        self.drag_start = None
        self.diffused_flag = True
        self.reflected_flag = True
        self.bed_flag = True
        self.depth_flag = True
        self.sky_flag = True
        self.apply_flags()

        # Run
        self._timer = app.Timer('auto', connect=self.on_timer, start=True)
        self.activate_zoom()
        self.show()

    def apply_flags(self):
        self.program["u_diffused_mult"] = 0.5 if self.diffused_flag else 0
        self.program["u_reflected_mult"] = 1.0 if self.reflected_flag else 0
        self.program["u_bed_mult"] = 1 if self.bed_flag else 0
        self.program["u_depth_mult"] = 1 if self.depth_flag else 0
        self.program["u_sky_mult"] = 1 if self.sky_flag else 0

    def set_camera(self):
        rotation = np.zeros((4, 4), dtype=np.float32)
        rotation[3, 3] = 1
        rotation[0, :3] = np.cross(self.up, self.camera)
        rotation[1, :3] = self.up
        rotation[2, :3] = self.camera
        world_view = rotation
        self.program['u_world_view'] = world_view.T
        self.program_point['u_world_view'] = world_view.T

    def rotate_camera(self, shift):
        right = np.cross(self.up, self.camera)
        new_camera = self.camera - right * shift[0] + self.up * shift[1]
        new_up = self.up - self.camera * shift[0]
        self.camera = normalize(new_camera)
        self.up = normalize(new_up)
        self.up = np.cross(self.camera, np.cross(self.up, self.camera))

    def activate_zoom(self):
        """
            Эта функция вызывается при установке размера окна
            1.Читаем размер окна
            2.Передаем размер окна в OpenGL
        """
        self.width, self.height = self.size
        gloo.set_viewport(0, 0, *self.physical_size)

    def add_wave_center(self, center):
        if self.surface_class is None:
            return
        pos_x = 1.5 * (center[0] - self.physical_size[0] / 2) / self.physical_size[0]
        pos_y = 1.5 * (-(center[1] - self.physical_size[1] / 2) / self.physical_size[1])
        self.surface_wave_list = [sf for sf in self.surface_wave_list if not sf.is_dead()]
        self.surface_wave_list.append(self.surface_class(center=(pos_x, pos_y)))

    def get_hieght_and_normal(self):
        height_total = None
        grad_total = None
        sf_len = 0
        for sf in self.surface_wave_list:
            sf_len += 1
            height, grad = sf.height_and_normal()
            if height_total is None:
                height_total = height
            else:
                height_total += height
            if grad_total is None:
                grad_total = grad
            else:
                grad_total += grad
        if height_total is None:
            avg_height, avg_grad = self.surface.height_and_normal()
        else:
            avg_height = height_total / sf_len
            avg_grad = grad_total / sf_len
        return avg_height, avg_grad

    def on_draw(self, event):
        # Все пиксели устанавливаются в значение clear_color,
        gloo.clear()
        # Читаем положение высот для текущего времени
        height, grad = self.get_hieght_and_normal()
        self.program["a_height"] = height
        self.program["a_normal"] = grad
        gloo.set_state(depth_test=True)
        self.program.draw('triangles', self.triangles)
        if self.are_points_visible:
            self.program_point["a_height"] = height
            gloo.set_state(depth_test=False)
            self.program_point.draw('points')

    def on_timer(self, event):
        self.surface.propagate(0.01)
        for sf in self.surface_wave_list:
            sf.propagate(0.01)
        self.update()

    def on_resize(self, event):
        self.activate_zoom()

    def on_key_press(self, event):
        if event.key == 'Escape':
            self.close()
        elif event.key == ' ':
            self.are_points_visible = not self.are_points_visible
            print("Show lattice vertices:", self.are_points_visible)
        elif event.key == '1':
            self.diffused_flag = not self.diffused_flag
            print("Show sun diffused light:", self.diffused_flag)
            self.apply_flags()
        elif event.key == '2':
            self.bed_flag = not self.bed_flag
            print("Show refracted image of seabed:", self.bed_flag)
            self.apply_flags()
        elif event.key == '3':
            self.depth_flag = not self.depth_flag
            print("Show ambient light in water:", self.depth_flag)
            self.apply_flags()
        elif event.key == '4':
            self.sky_flag = not self.sky_flag
            print("Show reflected image of sky:", self.sky_flag)
            self.apply_flags()
        elif event.key == '5':
            self.reflected_flag = not self.reflected_flag
            print("Show reflected image of sun:", self.reflected_flag)
            self.apply_flags()
        elif event.key == 'Up':
            self.program["u_bed_depth"] += 0.1
        elif event.key == 'Down':
            self.program["u_bed_depth"] -= 0.1

    def screen_to_gl_coordinates(self, pos):
        return 2 * np.array(pos) / np.array(self.size) - 1

    def on_mouse_press(self, event):
        self.drag_start = self.screen_to_gl_coordinates(event.pos)
        self.add_wave_center(event.pos)

    def on_mouse_move(self, event):
        if not self.drag_start is None:
            pos = self.screen_to_gl_coordinates(event.pos)
            self.rotate_camera(pos - self.drag_start)
            self.drag_start = pos
            self.set_camera()
            self.update()

    def on_mouse_release(self, event):
        self.drag_start = None

if __name__ == '__main__':
    c = Canvas(PlaneWaves(), BedLiner())
    # c = Canvas(CircularWaves(), BedLiner(), new_waves_class=CircularWaves)
    # c = Canvas(CircularWaves(), BedLiner())
    # c = Canvas(PlaneWaves(), BedCircular())
    app.run()
