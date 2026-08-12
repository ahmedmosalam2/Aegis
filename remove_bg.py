from PIL import Image
import sys

try:
    img = Image.open('Aegis.png').convert('RGBA')
    datas = img.getdata()

    newData = []
    # Tolerance for black (adjust if some edges remain)
    tolerance = 25
    for item in datas:
        if item[0] < tolerance and item[1] < tolerance and item[2] < tolerance:
            # Replace black with transparent
            newData.append((item[0], item[1], item[2], 0))
        else:
            newData.append(item)

    img.putdata(newData)
    img.save('Aegis_transparent.png', 'PNG')
    print("Successfully created Aegis_transparent.png")
except Exception as e:
    print(f"Error: {e}")
