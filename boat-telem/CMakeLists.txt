cmake_minimum_required(VERSION 3.16)
project(boat-telem CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)

find_package(Threads REQUIRED)

add_executable(boat-telem src/main.cpp)
target_include_directories(boat-telem PRIVATE src)
target_link_libraries(boat-telem PRIVATE Threads::Threads)

enable_testing()
foreach(t nmea_parser ina219_calc fusion_staleness)
  add_executable(test_${t} test/test_${t}.cpp)
  target_include_directories(test_${t} PRIVATE src)
  target_link_libraries(test_${t} PRIVATE Threads::Threads)
  # tests are assert-based: strip NDEBUG so Release builds can't compile the
  # assertions out and pass vacuously
  target_compile_options(test_${t} PRIVATE -UNDEBUG)
  add_test(NAME ${t} COMMAND test_${t})
endforeach()
