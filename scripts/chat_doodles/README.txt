Chat wallpaper generator (optional - only needed if you want to change the pattern)
-----------------------------------------------------------------------------------
Needs: pip install pillow numpy scipy scikit-image opencv-python
Run from this folder:   python make_chat_doodles.py 7 ../../core/static/core/images/chat-doodles.png
  - first number = layout seed (try other numbers for a different arrangement)
  - src/ holds the four hand-drawn doodles (pencil, flower, book, heart)
  - the affirmations and extra symbols are drawn inside make_chat_doodles.py
Colour and strength are set in CSS, not here: --doodle-color / --doodle-opacity in student-dashboard.html.
