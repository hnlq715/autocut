import logging
import os
import re
import datetime
from fractions import Fraction

import srt
from moviepy import editor

from . import utils


# Merge videos
class Merger:
    def __init__(self, args):
        self.args = args

    def write_md(self, videos):
        md = utils.MD(self.args.inputs[0], self.args.encoding)
        num_tasks = len(md.tasks())
        # Not overwrite if already marked as down or no new videos
        if md.done_editing() or num_tasks == len(videos) + 1:
            return

        md.clear()
        md.add_done_editing(False)
        md.add("\nSelect the files that will be used to generate `autocut_final.mp4`\n")
        base = lambda fn: os.path.basename(fn)
        for f in videos:
            md_fn = utils.change_ext(f, "md")
            video_md = utils.MD(md_fn, self.args.encoding)
            # select a few words to scribe the video
            desc = ""
            if len(video_md.tasks()) > 1:
                for _, t in video_md.tasks()[1:]:
                    m = re.findall(r"\] (.*)", t)
                    if m and "no speech" not in m[0].lower():
                        desc += m[0] + " "
                    if len(desc) > 50:
                        break
            md.add_task(
                False,
                f'[{base(f)}]({base(md_fn)}) {"[Edited]" if video_md.done_editing() else ""} {desc}',
            )
        md.write()

    def run(self):
        md_fn = self.args.inputs[0]
        md = utils.MD(md_fn, self.args.encoding)
        if not md.done_editing():
            return

        videos = []
        for m, t in md.tasks():
            if not m:
                continue
            m = re.findall(r"\[(.*)\]", t)
            if not m:
                continue
            fn = os.path.join(os.path.dirname(md_fn), m[0])
            logging.info(f"Loading {fn}")
            videos.append(editor.VideoFileClip(fn))

        dur = sum([v.duration for v in videos])
        logging.info(f"Merging into a video with {dur / 60:.1f} min length")

        merged = editor.concatenate_videoclips(videos)
        fn = os.path.splitext(md_fn)[0] + "_merged.mp4"
        merged.write_videofile(
            fn, audio_codec="aac", bitrate=self.args.bitrate
        )  # logger=None,
        logging.info(f"Saved merged video to {fn}")


# Cut media
class Cutter:
    def __init__(self, args):
        self.args = args

    def run(self):
        fns = {"srt": None, "media": None, "md": None}
        for fn in self.args.inputs:
            ext = os.path.splitext(fn)[1][1:]
            fns[ext if ext in fns else "media"] = fn

        assert fns["media"], "must provide a media filename"
        assert fns["md"], "must provide a srt filename"

        is_video_file = utils.is_video(fns["media"])
        outext = "mp4" if is_video_file else "mp3"
        output_fn = utils.change_ext(utils.add_cut(fns["media"]), outext)
        if utils.check_exists(output_fn, self.args.force):
            return

        if fns["md"]:
            md = utils.MD(fns["md"], self.args.encoding)
            if not md.done_editing():
                return
            subs = []
            for mark, sent in md.tasks():
                if not mark:
                    continue

                ss = sent.strip().split("|")
                if len(ss) == 5:
                    subs.append(srt.Subtitle(ss[1], datetime.timedelta(float(ss[2])), datetime.timedelta(float(ss[3])), ss[4]))
            logging.info(f'Cut {fns["media"]} based on {fns["srt"]} and {fns["md"]}')
        else:
            logging.info(f'Cut {fns["media"]} based on {fns["srt"]}')

        segments = []
        # Avoid disordered subtitles
        subs.sort(key=lambda x: x.start)

        for x in subs:
            if len(segments) == 0:
                segments.append(
                    {"start": x.start.total_seconds(), "end": x.end.total_seconds()}
                )
            else:
                if x.start.total_seconds() - segments[-1]["end"] < 0.5:
                    segments[-1]["end"] = x.end.total_seconds()
                else:
                    segments.append(
                        {"start": x.start.total_seconds(), "end": x.end.total_seconds()}
                    )

        # update srt file
        print(subs)
        subs = convert_subtitles(subs)
        print(subs)
        srtt = srt.compose(subs)
        srtName = str(utils.change_ext(fns["media"], "srt"))
        with open(srtName, "w") as f:
            f.write(srtt)

        fcp_xml("Autocut", fns["media"], utils.change_ext(fns["media"], "fcpxml"), subs)

        # if is_video_file:
        #     media = editor.VideoFileClip(fns["media"])
        # else:
        #     media = editor.AudioFileClip(fns["media"])

        # # Add a fade between two clips. Not quite necessary. keep code here for reference
        # # fade = 0
        # # segments = _expand_segments(segments, fade, 0, video.duration)
        # # clips = [video.subclip(
        # #         s['start'], s['end']).crossfadein(fade) for s in segments]
        # # final_clip = editor.concatenate_videoclips(clips, padding = -fade)

        # clips = [media.subclip(s["start"], s["end"]) for s in segments]
        # if is_video_file:
        #     final_clip: editor.VideoClip = editor.concatenate_videoclips(clips)
        #     logging.info(
        #         f"Reduced duration from {media.duration:.1f} to {final_clip.duration:.1f}"
        #     )

        #     aud = final_clip.audio.set_fps(44100)
        #     final_clip = final_clip.without_audio().set_audio(aud)
        #     final_clip = final_clip.fx(editor.afx.audio_normalize)

        #     # an alternative to birate is use crf, e.g. ffmpeg_params=['-crf', '18']
        #     final_clip.write_videofile(
        #         output_fn, audio_codec="aac", bitrate=self.args.bitrate,
        #         ffmpeg_params=['-filter_complex', "lut3d=/tmp/fix.cube,lut3d=/tmp/athena.cube,lut3d=/tmp/film.cube,subtitles=input.srt"]
        #     )
        # else:
        #     final_clip: editor.AudioClip = editor.concatenate_audioclips(clips)
        #     logging.info(
        #         f"Reduced duration from {media.duration:.1f} to {final_clip.duration:.1f}"
        #     )

        #     final_clip = final_clip.fx(editor.afx.audio_normalize)
        #     final_clip.write_audiofile(
        #         output_fn, codec="libmp3lame", fps=44100, bitrate=self.args.bitrate
        #     )

        # media.close()
        # logging.info(f"Saved media to {output_fn}")

def convert_subtitles(subtitles: list[srt.Subtitle]):
    result = []
    start_time = datetime.timedelta(0)

    for i, subtitle in enumerate(subtitles):
        end_time = subtitle.end
        subtitle_text = subtitle.content
        
        duration = end_time - subtitle.start
        result.append(srt.Subtitle(i, start_time, start_time + duration, subtitle_text))
        
        start_time += duration
    
    return result

def fraction(_a: float, tb: Fraction) -> str:
    if _a == 0:
        return "0s"

    a = Fraction(_a)
    frac = Fraction(a, tb).limit_denominator()
    num = frac.numerator
    dem = frac.denominator

    if dem < 3000:
        factor = int(3000 / dem)

        if factor == 3000 / dem:
            num *= factor
            dem *= factor
        else:
            # Good enough but has some error that are impacted at speeds such as 150%.
            total = Fraction(0)
            while total < frac:
                total += Fraction(1, 30)
            num = total.numerator
            dem = total.denominator

    return f"{num}/{dem}s"

def indent(base: int, *lines: str) -> str:
    new_lines = ""
    for line in lines:
        new_lines += ("\t" * base) + line + "\n"
    return new_lines

def fcp_xml(group_name: str, input: str, output: str, subs: list[srt.Subtitle]) -> None:
    tb = 25
    total_dur = subs[-1].end.total_seconds()
    pathurl = input
    #4k
    width, height = 3840, 2160
    name = os.path.basename(input)
    colorspace = "1-1-1 (Rec. 709)"

    with open(output, "w", encoding="utf-8") as outfile:
        outfile.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        outfile.write("<!DOCTYPE fcpxml>\n\n")
        outfile.write('<fcpxml version="1.9">\n')
        outfile.write("\t<resources>\n")
        outfile.write(
            f'\t\t<format id="r1" name="FFVideoFormat{height}p{float(tb)}" '
            f'frameDuration="{fraction(1, tb)}" '
            f'width="{width}" height="{height}" '
            f'colorSpace="{colorspace}"/>\n'
        )
        outfile.write(
            f'\t\t<asset id="r2" name="{name}" start="0s" hasVideo="1" format="r1" '
            'hasAudio="1" audioSources="1" audioChannels="2" '
            f'duration="{fraction(total_dur, tb)}">\n'
        )
        outfile.write(
            f'\t\t\t<media-rep kind="original-media" src="{pathurl}"></media-rep>\n'
        )
        outfile.write("\t\t</asset>\n")
        outfile.write("\t</resources>\n")
        outfile.write("\t<library>\n")
        outfile.write(f'\t\t<event name="{group_name}">\n')
        outfile.write(f'\t\t\t<project name="{name}">\n')
        outfile.write(
            indent(
                4,
                '<sequence format="r1" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">',
                "\t<spine>",
            )
        )

        last_dur = 0.0
        for clip in subs:

            clip_dur = clip.end - clip.start
            dur = fraction(clip_dur.total_seconds(), tb)

            close = ""

            if last_dur == 0:
                outfile.write(
                    indent(
                        6,
                        f'<asset-clip name="{name}" offset="0s" ref="r2" duration="{dur}" tcFormat="NDF"{close}>',
                    )
                )
            else:
                start = fraction(clip.start.total_seconds(), tb)
                off = fraction(last_dur, tb)
                outfile.write(
                    indent(
                        6,
                        f'<asset-clip name="{name}" offset="{off}" ref="r2" '
                        + f'duration="{dur}" start="{start}" '
                        + f'tcFormat="NDF"{close}>',
                    )
                )

            last_dur += clip_dur.total_seconds()

        outfile.write("\t\t\t\t\t</spine>\n")
        outfile.write("\t\t\t\t</sequence>\n")
        outfile.write("\t\t\t</project>\n")
        outfile.write("\t\t</event>\n")
        outfile.write("\t</library>\n")
        outfile.write("</fcpxml>\n")

        logging.info(f"Saved fcpxml to {output}")