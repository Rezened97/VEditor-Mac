import os
import sys
import re
import math
import subprocess
from pathlib import Path
from itertools import product
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Drag & Drop support con fallback sicuro
DND_AVAILABLE = False
DND_FILES = None

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except Exception:
    TkinterDnD = None
    DND_AVAILABLE = False

# Use imageio-ffmpeg bundled ffmpeg binary
try:
    from imageio_ffmpeg import get_ffmpeg_exe
    ffmpeg_bin = get_ffmpeg_exe()
except ImportError:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Errore", "Installa imageio-ffmpeg: pip install imageio-ffmpeg")
    root.destroy()
    sys.exit(1)

# Prepare startupinfo to hide ffmpeg console on Windows
STARTUPINFO = None
if os.name == 'nt':
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    STARTUPINFO = si

# -------- Costanti UI Moderne --------
BG_APP = "#F3F4F6"
BG_CARD = "#FFFFFF"
COLOR_ACCENT = "#3B82F6"
COLOR_TEXT = "#1F2937"
COLOR_DANGER = "#EF4444"
FONT_MAIN = ('Segoe UI', 10)
FONT_TITLE = ('Segoe UI', 12, 'bold')
FONT_LOG = ('Consolas', 9)


# -------- Helpers --------
def safe_unlink(path: Path):
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def get_media_duration(path: Path) -> float:
    cmd = [ffmpeg_bin, '-i', str(path)]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=STARTUPINFO
    )
    _, err = proc.communicate()
    text = err.decode(errors='ignore')
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", text)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def adjust_audio_speed(audio: Path, target_duration: float) -> Path:
    orig = get_media_duration(audio)
    if orig <= 0 or target_duration <= 0:
        return audio

    speed = orig / target_duration
    filters = []
    factor = speed

    while factor < 0.5:
        filters.append('atempo=0.5')
        factor /= 0.5
    while factor > 2.0:
        filters.append('atempo=2.0')
        factor /= 2.0

    filters.append(f'atempo={factor}')
    filt = ','.join(filters)

    out_file = audio.with_name(f"{audio.stem}_adj{audio.suffix}")
    cmd = [
        ffmpeg_bin, '-y',
        '-i', str(audio),
        '-filter:a', filt,
        str(out_file)
    ]
    subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)
    return out_file


def ffconcat_escape(path: Path) -> str:
    return str(path.resolve().as_posix()).replace("'", r"'\''")


def write_ffconcat_file(inputs, list_file: Path):
    with open(list_file, 'w', encoding='utf-8', newline='\n') as f:
        f.write("ffconcat version 1.0\n")
        for p in inputs:
            f.write(f"file '{ffconcat_escape(p)}'\n")


def get_stream_signature(path: Path):
    cmd = [ffmpeg_bin, '-hide_banner', '-i', str(path)]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=STARTUPINFO
    )
    _, err = proc.communicate()
    text = err.decode(errors='ignore')

    video_lines = re.findall(r"Stream #\d+:\d+(?:\([^)]+\))?: Video: ([^\n\r]+)", text)
    audio_lines = re.findall(r"Stream #\d+:\d+(?:\([^)]+\))?: Audio: ([^\n\r]+)", text)

    sig = {
        'video_streams': len(video_lines),
        'audio_streams': len(audio_lines),
        'video_codec': None,
        'width': None,
        'height': None,
        'fps': None,
        'tbn': None,
        'audio_codec': None,
        'sample_rate': None,
        'channels': None,
    }

    if video_lines:
        v = video_lines[0]
        sig['video_codec'] = v.split(',')[0].strip().lower()

        m_res = re.search(r'(\d{2,5})x(\d{2,5})', v)
        if m_res:
            sig['width'] = int(m_res.group(1))
            sig['height'] = int(m_res.group(2))

        m_fps = re.search(r'(\d+(?:\.\d+)?)\s+fps', v)
        if m_fps:
            sig['fps'] = float(m_fps.group(1))

        m_tbn = re.search(r'(\d+(?:\.\d+)?)\s+tbn', v)
        if m_tbn:
            try:
                sig['tbn'] = float(m_tbn.group(1))
            except ValueError:
                sig['tbn'] = None

    if audio_lines:
        a = audio_lines[0]
        sig['audio_codec'] = a.split(',')[0].strip().lower()

        m_sr = re.search(r'(\d+)\s+Hz', a)
        if m_sr:
            sig['sample_rate'] = int(m_sr.group(1))

        for ch in ('mono', 'stereo', '5.1', '7.1'):
            if re.search(rf'\b{re.escape(ch)}\b', a):
                sig['channels'] = ch
                break

        if sig['channels'] is None:
            m_ch = re.search(r'(\d+)\s+channels?', a)
            if m_ch:
                sig['channels'] = m_ch.group(1)

    return sig


def approx_equal(a, b, tol=0.01):
    if a is None or b is None:
        return a == b
    return math.isclose(a, b, abs_tol=tol)


def can_fast_concat(inputs, expect_audio: bool):
    if not inputs:
        return False

    base = get_stream_signature(inputs[0])

    if base['video_streams'] != 1 or base['video_codec'] is None:
        return False

    if expect_audio:
        if base['audio_streams'] != 1 or base['audio_codec'] is None:
            return False

    for p in inputs[1:]:
        cur = get_stream_signature(p)

        if cur['video_streams'] != base['video_streams']:
            return False
        if cur['video_codec'] != base['video_codec']:
            return False
        if cur['width'] != base['width'] or cur['height'] != base['height']:
            return False
        if not approx_equal(cur['fps'], base['fps'], 0.05):
            return False
        if not approx_equal(cur['tbn'], base['tbn'], 1.0):
            return False

        if expect_audio:
            if cur['audio_streams'] != base['audio_streams']:
                return False
            if cur['audio_codec'] != base['audio_codec']:
                return False
            if cur['sample_rate'] != base['sample_rate']:
                return False
            if cur['channels'] != base['channels']:
                return False

    return True


def get_fallback_target(inputs):
    sig = get_stream_signature(inputs[0])
    width = sig['width'] or 1080
    height = sig['height'] or 1920
    fps = sig['fps'] or 30.0
    return width, height, fps


# -------- Process functions --------
def process_concat_internal_fallback(inputs, output: Path):
    width, height, fps = get_fallback_target(inputs)

    cmd = [ffmpeg_bin, '-y']
    for p in inputs:
        cmd += ['-i', str(p)]

    parts = []
    concat_inputs = []

    for i in range(len(inputs)):
        parts.append(
            f'[{i}:v:0]fps={fps},scale={width}:{height}:flags=lanczos,format=yuv420p,setsar=1[v{i}]'
        )
        parts.append(
            f'[{i}:a:0]aresample=44100,'
            f'aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a{i}]'
        )
        concat_inputs.append(f'[v{i}][a{i}]')

    parts.append(''.join(concat_inputs) + f'concat=n={len(inputs)}:v=1:a=1[v][a]')

    cmd += [
        '-filter_complex', ';'.join(parts),
        '-map', '[v]',
        '-map', '[a]',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '18',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-ar', '44100',
        '-ac', '2',
        '-movflags', '+faststart',
        str(output)
    ]

    subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)


def process_concat_external_fallback(inputs, audio: Path, output: Path):
    width, height, fps = get_fallback_target(inputs)
    total_dur = sum(get_media_duration(p) for p in inputs)
    adj_audio = adjust_audio_speed(audio, total_dur)

    try:
        cmd = [ffmpeg_bin, '-y']
        for p in inputs:
            cmd += ['-i', str(p)]
        cmd += ['-i', str(adj_audio)]

        parts = []
        concat_inputs = []

        for i in range(len(inputs)):
            parts.append(
                f'[{i}:v:0]fps={fps},scale={width}:{height}:flags=lanczos,format=yuv420p,setsar=1[v{i}]'
            )
            concat_inputs.append(f'[v{i}]')

        parts.append(''.join(concat_inputs) + f'concat=n={len(inputs)}:v=1:a=0[v]')

        cmd += [
            '-filter_complex', ';'.join(parts),
            '-map', '[v]',
            '-map', f'{len(inputs)}:a:0',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '18',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-ar', '44100',
            '-ac', '2',
            '-movflags', '+faststart',
            '-shortest',
            str(output)
        ]

        subprocess.run(cmd, check=True, startupinfo=STARTUPINFO)
    finally:
        if adj_audio != audio:
            safe_unlink(adj_audio)


def process_concat_internal(inputs, output: Path):
    if not can_fast_concat(inputs, expect_audio=True):
        process_concat_internal_fallback(inputs, output)
        return

    list_file = output.with_name(f"{output.stem}_list.ffconcat")
    write_ffconcat_file(inputs, list_file)

    try:
        cmd_concat = [
            ffmpeg_bin, '-y',
            '-fflags', '+genpts',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(list_file),
            '-c', 'copy',
            '-movflags', '+faststart',
            '-avoid_negative_ts', 'make_zero',
            str(output)
        ]
        subprocess.run(cmd_concat, check=True, startupinfo=STARTUPINFO)
    finally:
        safe_unlink(list_file)


def process_concat_external(inputs, audio: Path, output: Path):
    if not can_fast_concat(inputs, expect_audio=False):
        process_concat_external_fallback(inputs, audio, output)
        return

    list_file = output.with_name(f"{output.stem}_list.ffconcat")
    temp_vid = output.with_name(f"{output.stem}_video_only.mp4")
    adj_audio = audio

    write_ffconcat_file(inputs, list_file)

    try:
        cmd_concat = [
            ffmpeg_bin, '-y',
            '-fflags', '+genpts',
            '-f', 'concat',
            '-safe', '0',
            '-i', str(list_file),
            '-map', '0:v:0',
            '-c:v', 'copy',
            '-movflags', '+faststart',
            '-avoid_negative_ts', 'make_zero',
            str(temp_vid)
        ]
        subprocess.run(cmd_concat, check=True, startupinfo=STARTUPINFO)

        total_dur = sum(get_media_duration(p) for p in inputs)
        adj_audio = adjust_audio_speed(audio, total_dur)

        cmd_mux = [
            ffmpeg_bin, '-y',
            '-i', str(temp_vid),
            '-i', str(adj_audio),
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-ar', '44100',
            '-ac', '2',
            '-movflags', '+faststart',
            '-avoid_negative_ts', 'make_zero',
            '-shortest',
            str(output)
        ]
        subprocess.run(cmd_mux, check=True, startupinfo=STARTUPINFO)

    finally:
        safe_unlink(list_file)
        safe_unlink(temp_vid)
        if adj_audio != audio:
            safe_unlink(adj_audio)


# -------- GUI classes --------
class FileList(ttk.Frame):
    def __init__(self, parent, title, filetypes):
        super().__init__(parent, style='Card.TFrame')
        self.filetypes = filetypes
        self.storage = []
        self.enabled = True
        self.dnd_ready = False

        header_frame = ttk.Frame(self, style='Card.TFrame')
        header_frame.pack(fill='x', padx=10, pady=(10, 5))

        ttk.Label(
            header_frame,
            text=title,
            font=FONT_TITLE,
            background=BG_CARD,
            foreground=COLOR_TEXT
        ).pack(side='left')

        self.load_btn = ttk.Button(
            header_frame,
            text='+ Aggiungi',
            width=10,
            command=self.load_files,
            style='Outline.TButton'
        )
        self.load_btn.pack(side='right')

        drop_text = '📁 Trascina i file qui oppure usa + Aggiungi' if DND_AVAILABLE else '📁 Usa + Aggiungi (drag & drop non disponibile)'
        self.drop_area = tk.Label(
            self,
            text=drop_text,
            font=('Segoe UI', 10, 'italic'),
            bg='#F9FAFB',
            fg='#9CA3AF',
            relief='solid',
            borderwidth=1,
            height=3
        )
        self.drop_area.pack(fill='x', padx=10, pady=5)

        if DND_AVAILABLE:
            try:
                self.drop_area.drop_target_register(DND_FILES)
                self.drop_area.dnd_bind('<<Drop>>', self.handle_drop)
                self.dnd_ready = True
            except Exception:
                self.dnd_ready = False
                self.drop_area.configure(text='📁 Usa + Aggiungi (drag & drop non inizializzato)')

        self.list_frame = tk.Frame(self, bg=BG_CARD)
        self.list_frame.pack(fill='x', padx=10, pady=(0, 10))
        self.list_frame.columnconfigure(0, weight=1)

    def load_files(self):
        if not self.enabled:
            return
        files = filedialog.askopenfilenames(filetypes=self.filetypes)
        self.add_files(files)

    def handle_drop(self, event):
        if not self.enabled:
            return
        try:
            files = self.tk.splitlist(event.data)
        except Exception:
            files = [event.data]
        self.add_files(files)

    def add_files(self, files):
        if not self.enabled:
            return
        changed = False
        for f in files:
            p = Path(f)
            if p.is_file() and p not in self.storage:
                self.storage.append(p)
                changed = True
        if changed:
            self.refresh_list()

    def remove_file(self, index):
        if 0 <= index < len(self.storage):
            del self.storage[index]
            self.refresh_list()

    def refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()

        for idx, p in enumerate(self.storage):
            row_bg = '#F3F4F6' if idx % 2 == 0 else BG_CARD
            row = tk.Frame(self.list_frame, bg=row_bg, pady=4, padx=5)
            row.pack(fill='x', pady=1)

            tk.Label(
                row,
                text=p.name,
                bg=row_bg,
                fg=COLOR_TEXT,
                font=FONT_MAIN,
                anchor='w'
            ).pack(side='left', fill='x', expand=True)

            del_btn = tk.Button(
                row,
                text='✕',
                bg=row_bg,
                fg=COLOR_DANGER,
                bd=0,
                font=('Segoe UI', 10, 'bold'),
                activebackground=row_bg,
                activeforeground='#B91C1C',
                cursor='hand2',
                command=lambda i=idx: self.remove_file(i)
            )
            del_btn.pack(side='right', padx=5)

            if not self.enabled:
                del_btn.config(state='disabled')

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        state = 'normal' if enabled else 'disabled'
        self.load_btn.configure(state=state)

        if enabled:
            if self.dnd_ready:
                self.drop_area.configure(
                    bg='#F9FAFB',
                    fg='#6B7280',
                    text='📁 Trascina i file qui oppure usa + Aggiungi'
                )
            else:
                self.drop_area.configure(
                    bg='#F9FAFB',
                    fg='#6B7280',
                    text='📁 Usa + Aggiungi'
                )
        else:
            self.drop_area.configure(
                bg='#E5E7EB',
                fg='#9CA3AF',
                text='🚫 Sezione Disattivata'
            )

        self.refresh_list()


class MontageGUI(TkinterDnD.Tk if DND_AVAILABLE else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Automazione Montaggio Video')
        self.geometry('750x850')
        self.configure(bg=BG_APP)
        self.minsize(600, 700)
        self.setup_styles()

        self.mode_var = tk.StringVar(value='I')
        self.use_lead_var = tk.BooleanVar(value=True)

        self.output_dir = Path.cwd() / 'video_finali'
        self.output_dir.mkdir(exist_ok=True)

        header = tk.Frame(self, bg=COLOR_ACCENT, height=60)
        header.pack(fill='x')
        tk.Label(
            header,
            text="🎬 Generatore Video Multiplo",
            bg=COLOR_ACCENT,
            fg="white",
            font=('Segoe UI', 16, 'bold')
        ).pack(pady=15)

        container = ttk.Frame(self)
        container.pack(fill='both', expand=True)

        self.canvas = tk.Canvas(container, bg=BG_APP, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)

        vsb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)

        self.scroll_frame = tk.Frame(self.canvas, bg=BG_APP)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scroll_frame, anchor='nw')

        self.scroll_frame.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width))

        self._bind_mousewheel_events()
        self.build_ui()

    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=FONT_MAIN, background=BG_APP)
        style.configure('Card.TFrame', background=BG_CARD)
        style.configure('TRadiobutton', background=BG_APP, font=FONT_MAIN)
        style.configure('TCheckbutton', background=BG_APP, font=FONT_MAIN)
        style.configure('TButton', font=FONT_MAIN, padding=5)
        style.configure('Outline.TButton', background=BG_CARD, foreground=COLOR_TEXT)
        style.configure('Accent.TButton', font=('Segoe UI', 12, 'bold'), background=COLOR_ACCENT, foreground='white', padding=10)
        style.map('Accent.TButton', background=[('active', '#2563EB')])

    def _bind_mousewheel_events(self):
        self.bind_all('<MouseWheel>', self._on_mousewheel, add='+')
        self.bind_all('<Button-4>', self._on_mousewheel, add='+')
        self.bind_all('<Button-5>', self._on_mousewheel, add='+')

    def _mousewheel_target_ok(self, event):
        try:
            widget_under_pointer = self.winfo_containing(event.x_root, event.y_root)
            if widget_under_pointer is None:
                return False

            w = widget_under_pointer
            while w is not None:
                if w == self.canvas:
                    return True
                try:
                    parent_name = w.winfo_parent()
                    if not parent_name:
                        break
                    w = w.nametowidget(parent_name)
                except Exception:
                    break
            return False
        except Exception:
            return True

    def _on_mousewheel(self, event):
        if not self._mousewheel_target_ok(event):
            return

        step = 0

        if getattr(event, 'num', None) == 4:
            step = -1
        elif getattr(event, 'num', None) == 5:
            step = 1
        else:
            delta = getattr(event, 'delta', 0)
            if delta > 0:
                step = -1
            elif delta < 0:
                step = 1
            else:
                step = 0

        if step != 0:
            self.canvas.yview_scroll(step, 'units')

    def build_ui(self):
        content = tk.Frame(self.scroll_frame, bg=BG_APP)
        content.pack(fill='both', expand=True, padx=20, pady=15)

        config_frame = tk.LabelFrame(
            content,
            text=" Impostazioni di Montaggio ",
            font=FONT_TITLE,
            bg=BG_APP,
            fg=COLOR_TEXT,
            padx=15,
            pady=10
        )
        config_frame.pack(fill='x', pady=(0, 15))

        tk.Label(config_frame, text="Sorgente Audio:", bg=BG_APP, font=FONT_MAIN).grid(row=0, column=0, sticky='w', pady=5)
        ttk.Radiobutton(config_frame, text='Mantieni Audio Interno', variable=self.mode_var, value='I', command=self.toggle_audio).grid(row=0, column=1, sticky='w', padx=10)
        ttk.Radiobutton(config_frame, text='Sostituisci con Audio Esterno', variable=self.mode_var, value='E', command=self.toggle_audio).grid(row=0, column=2, sticky='w', padx=10)

        tk.Label(config_frame, text="Struttura Video:", bg=BG_APP, font=FONT_MAIN).grid(row=1, column=0, sticky='w', pady=5)
        ttk.Checkbutton(config_frame, text='Includi LEAD tra Hook e Body', variable=self.use_lead_var, command=self.toggle_lead).grid(row=1, column=1, columnspan=2, sticky='w', padx=10)

        self.hooks_widget = FileList(content, '🎬 HOOK (Clip Iniziale)', [('Video files', '*.mp4 *.mov *.avi *.mkv')])
        self.hooks_widget.pack(fill='x', pady=8)

        self.leads_widget = FileList(content, '🔗 LEAD (Transizione/Ponte)', [('Video files', '*.mp4 *.mov *.avi *.mkv')])
        self.leads_widget.pack(fill='x', pady=8)

        self.bodies_widget = FileList(content, '📹 BODY (Contenuto Principale)', [('Video files', '*.mp4 *.mov *.avi *.mkv')])
        self.bodies_widget.pack(fill='x', pady=8)

        self.audios_widget = FileList(content, '🎵 AUDIO ESTERNO (Opzionale)', [('Audio files', '*.mp3 *.wav *.m4a')])
        self.audios_widget.pack(fill='x', pady=8)

        out_frame = tk.Frame(content, bg=BG_APP)
        out_frame.pack(fill='x', pady=15)

        tk.Label(out_frame, text='Cartella di salvataggio:', font=FONT_TITLE, bg=BG_APP, fg=COLOR_TEXT).pack(anchor='w')
        path_frame = tk.Frame(out_frame, bg=BG_CARD, highlightbackground='#D1D5DB', highlightthickness=1)
        path_frame.pack(fill='x', pady=5)

        self.out_label = tk.Label(path_frame, text=str(self.output_dir), bg=BG_CARD, fg=COLOR_TEXT, padx=10, pady=8, anchor='w')
        self.out_label.pack(side='left', fill='x', expand=True)

        ttk.Button(path_frame, text='Modifica...', command=self.change_output).pack(side='right', padx=5, pady=5)

        self.run_btn = ttk.Button(content, text='🚀 AVVIA MONTAGGIO VELOCE', style='Accent.TButton', command=self.run)
        self.run_btn.pack(fill='x', pady=10)

        log_container = tk.Frame(content, bg='#1E293B', bd=0, highlightthickness=0)
        log_container.pack(fill='both', expand=True, pady=(10, 0))

        tk.Label(log_container, text="Console Log:", bg='#1E293B', fg='#94A3B8', font=('Segoe UI', 9)).pack(anchor='w', padx=5, pady=2)

        self.txt_log = tk.Text(log_container, height=8, font=FONT_LOG, bg='#0F172A', fg='#38BDF8', bd=0, padx=10, pady=10)
        self.txt_log.pack(fill='both', expand=True)

        self.toggle_audio()
        self.toggle_lead()

    def toggle_audio(self):
        self.audios_widget.set_enabled(self.mode_var.get() == 'E')

    def toggle_lead(self):
        use_lead = self.use_lead_var.get()
        self.leads_widget.set_enabled(use_lead)

    def change_output(self):
        d = filedialog.askdirectory()
        if d:
            self.output_dir = Path(d)
            self.out_label.config(text=str(self.output_dir))

    def log(self, msg):
        self.txt_log.insert('end', f"> {msg}\n")
        self.txt_log.see('end')
        self.update_idletasks()

    def run(self):
        h = self.hooks_widget.storage
        l = self.leads_widget.storage
        b = self.bodies_widget.storage
        a = self.audios_widget.storage

        use_lead = self.use_lead_var.get()
        use_external_audio = self.mode_var.get() == 'E'

        if not h or not b:
            messagebox.showwarning("Attenzione", "Seleziona almeno un file HOOK e un file BODY.")
            return

        if use_lead and not l:
            messagebox.showwarning("Attenzione", "Hai attivato il LEAD, ma non hai selezionato nessun file per questa sezione.")
            return

        if use_external_audio and not a:
            messagebox.showwarning("Attenzione", "Hai selezionato l'audio esterno, ma non hai caricato nessun file audio.")
            return

        count = 1

        if use_lead:
            combos = product(h, l, b, a) if use_external_audio else product(h, l, b)
        else:
            combos = product(h, b, a) if use_external_audio else product(h, b)

        try:
            self.run_btn.config(state='disabled')
            self.log("Avvio del processo con controllo automatico compatibilità clip...")

            for combo in combos:
                if use_lead:
                    if use_external_audio:
                        hook, lead, body, audio = combo
                        inputs = [hook, lead, body]
                    else:
                        hook, lead, body = combo
                        audio = None
                        inputs = [hook, lead, body]
                else:
                    if use_external_audio:
                        hook, body, audio = combo
                    else:
                        hook, body = combo
                        audio = None
                    inputs = [hook, body]

                name = f"video{count}_hook{h.index(hook) + 1}"
                if use_lead:
                    name += f"_lead{l.index(lead) + 1}"
                name += f"_body{b.index(body) + 1}"
                if audio:
                    name += f"_audio{a.index(audio) + 1}"

                out_path = self.output_dir / f"{name}.mp4"

                sig_ok = can_fast_concat(inputs, expect_audio=not use_external_audio)
                if sig_ok:
                    self.log(f"Elaborazione: {name}.mp4 [fast copy]")
                else:
                    self.log(f"Elaborazione: {name}.mp4 [fallback sicuro]")

                if use_external_audio:
                    process_concat_external(inputs, audio, out_path)
                else:
                    process_concat_internal(inputs, out_path)

                count += 1

            self.log("✅ PROCESSO COMPLETATO!")
            messagebox.showinfo("Completato", f"Generati {count - 1} video con controllo automatico!")

        except subprocess.CalledProcessError as e:
            self.log("❌ ERRORE FFMPEG!")
            messagebox.showerror("Errore FFmpeg", str(e))
        except Exception as e:
            self.log("❌ ERRORE IMPREVISTO!")
            messagebox.showerror("Errore imprevisto", str(e))
        finally:
            self.run_btn.config(state='normal')


if __name__ == '__main__':
    app = MontageGUI()
    app.mainloop()
