#!/usr/bin/env bash
# rec.sh OUT.mp4 SCRIPT  -> runs SCRIPT in a fullscreen terminal on :8 and records it
OUT=$1; SCRIPT=$2; export DISPLAY=:8
ffmpeg -v error -y -f x11grab -video_size 1920x1080 -framerate 30 -i :8 -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p "$OUT" & FP=$!
sleep 1
xfce4-terminal --disable-server --fullscreen --hide-menubar --hide-toolbar --hide-scrollbar --font="DejaVu Sans Mono 20" -x bash --noprofile --norc "$SCRIPT"
sleep 1; kill -INT $FP; wait $FP
