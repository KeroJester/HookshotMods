CXX = g++
CXXFLAGS = -O3 -fPIC -fvisibility=hidden -std=c++17 -Wall -Wextra -Wno-missing-field-initializers \
  -DVK_NO_PROTOTYPES \
  -I../vulkan_headers_src/include \
  -I/usr/include/SDL3 \
  -I../imgui_src \
  -I../imgui_src/backends

LDFLAGS = -shared -ldl -lpthread -lSDL3

SRCS = \
  hookshot_overlay.cpp \
  ../imgui_src/imgui.cpp \
  ../imgui_src/imgui_draw.cpp \
  ../imgui_src/imgui_tables.cpp \
  ../imgui_src/imgui_widgets.cpp \
  ../imgui_src/backends/imgui_impl_sdl3.cpp \
  ../imgui_src/backends/imgui_impl_vulkan.cpp

OBJS = $(SRCS:.cpp=.o)
TARGET = libhookshot_overlay.so

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CXX) $(CXXFLAGS) -o $@ $^ $(LDFLAGS)

%.o: %.cpp param_tuner.inc player_param_keys.h guest_hooks.inc goron_hooks.inc song_of_healing.inc
	$(CXX) $(CXXFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)

.PHONY: all clean

# the overlay includes these
hookshot_overlay.o: $(wildcard *.inc) $(wildcard *.h)
